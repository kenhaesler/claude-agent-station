"""Async task scheduler: watches DAG, spawns employees, monitors streams."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig

from agent.coordinator.dag import TaskDAG, Task, TaskStatus
from agent.coordinator.employee_runner import run_employee, EmployeeResult
from agent.coordinator.stream_monitor import (
    EmployeeActivity,
    ConflictDetector,
    monitor_stream,
)
from agent.coordinator.guidance import send_guidance
from agent.coordinator.manager import (
    check_plan_usage_before_spawn,
    request_graceful_wrapup,
    handle_budget_exhaustion,
    RateLimitTracker,
    DEFAULT_MAX_USAGE_PERCENT,
)
from agent.coordinator.reporter import post_task_event, post_conflict, post_guidance

logger = logging.getLogger(__name__)


async def run_scheduler(dag: TaskDAG, config: CoordinatorConfig) -> None:
    """Main scheduling loop. Spawns employees for ready tasks, monitors progress."""
    semaphore = asyncio.Semaphore(config.max_concurrent)
    running: dict[str, asyncio.Task] = {}
    activities: dict[str, EmployeeActivity] = {}
    task_workspaces: dict[str, str] = {}  # task_id -> workspace path
    stop_events: dict[str, asyncio.Event] = {}
    conflict_detector = ConflictDetector()
    rate_limit_tracker = RateLimitTracker()
    next_employee_index = 0

    logger.info("Scheduler started: %d tasks, max_concurrent=%d", await dag.task_count(), config.max_concurrent)

    # Track usage check interval (don't check every loop iteration)
    usage_check_counter = 0
    usage_check_interval = 5  # Check every N loop iterations
    last_usage_ok = True

    while not await dag.all_done():
        # Periodic plan usage check
        usage_check_counter += 1
        if usage_check_counter % usage_check_interval == 0 and running:
            try:
                can_spawn, reason, snapshot = check_plan_usage_before_spawn(
                    config,
                    max_usage_percent=getattr(config, "max_usage_percent", DEFAULT_MAX_USAGE_PERCENT),
                )
                if not can_spawn and last_usage_ok:
                    # Usage just crossed the threshold — notify running employees
                    running_map: dict[str, tuple[int, str]] = {}
                    for tid, atask in activities.items():
                        ws = task_workspaces.get(tid, "")
                        running_map[tid] = (atask.employee_index, ws)
                    request_graceful_wrapup(config, dag, snapshot, running_map)
                    logger.warning("Plan usage threshold reached: %s", reason)
                last_usage_ok = can_spawn
            except Exception as e:
                logger.debug("Plan usage check failed (non-fatal): %s", e)

        # If budget exhausted via rate limit detection, stop spawning
        if rate_limit_tracker.is_budget_exhausted:
            if running:
                logger.warning(
                    "Budget exhausted — waiting for %d running employees to finish",
                    len(running),
                )
            else:
                logger.warning("Budget exhausted — no running employees, stopping scheduler")
                break
            # Don't spawn new tasks, just wait for running ones
            # Skip the spawn loop entirely
            done, _ = await asyncio.wait(
                running.values(),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=5.0,
            )
            for completed_future in done:
                task_id = completed_future.result()
                del running[task_id]
                if task_id in stop_events:
                    stop_events[task_id].set()
                    del stop_events[task_id]
                if task_id in activities:
                    activity = activities[task_id]
                    conflict_detector.clear(activity.employee_index)
                    del activities[task_id]
                task_workspaces.pop(task_id, None)
            continue

        # Spawn employees for ready tasks
        for task in await dag.ready_tasks():
            if task.id in running:
                continue

            # Check plan usage before spawning
            try:
                can_spawn, reason, snapshot = check_plan_usage_before_spawn(
                    config,
                    max_usage_percent=getattr(config, "max_usage_percent", DEFAULT_MAX_USAGE_PERCENT),
                )
                if not can_spawn:
                    logger.warning(
                        "Skipping task '%s': plan usage too high (%s)",
                        task.title, reason,
                    )
                    continue
            except Exception as e:
                logger.debug("Plan usage pre-spawn check failed (non-fatal): %s", e)

            await semaphore.acquire()
            employee_index = next_employee_index
            next_employee_index += 1

            # Setup workspace (worktree for non-zero employees)
            workspace = await _setup_task_workspace(task, config, employee_index)
            if not workspace:
                semaphore.release()
                await dag.mark_failed(task.id, "Failed to setup workspace")
                post_task_event(config, "task_failed", task)
                continue

            await dag.mark_running(task.id, employee_index, workspace)
            # Update the local DTO so downstream code (employee_runner) sees the workspace
            task.workspace = workspace
            task.employee_index = employee_index
            task_workspaces[task.id] = workspace
            post_task_event(config, "task_started", task)

            # Create activity tracker and stop event
            activity = EmployeeActivity(
                employee_index=employee_index,
                task_id=task.id,
            )
            activities[task.id] = activity
            stop_event = asyncio.Event()
            stop_events[task.id] = stop_event

            # Launch employee + monitor as a combined task
            running[task.id] = asyncio.create_task(
                _run_and_monitor(
                    task, config, employee_index, activity,
                    stop_event, conflict_detector, semaphore,
                    dag, rate_limit_tracker,
                )
            )

        if not running:
            # Nothing running, nothing ready — all blocked or done
            break

        # Wait for at least one task to complete
        done, _ = await asyncio.wait(
            running.values(),
            return_when=asyncio.FIRST_COMPLETED,
            timeout=5.0,
        )

        # Process completed tasks
        for completed_future in done:
            task_id = completed_future.result()
            del running[task_id]

            # Clean up
            if task_id in stop_events:
                stop_events[task_id].set()
                del stop_events[task_id]
            if task_id in activities:
                activity = activities[task_id]
                conflict_detector.clear(activity.employee_index)
                del activities[task_id]
            task_workspaces.pop(task_id, None)

        # Periodic conflict detection
        if config.conflict_detection:
            for task_id, activity in activities.items():
                conflict_detector.update(activity.employee_index, activity.files_touched)
                conflicts = conflict_detector.check_conflicts(activity.employee_index)
                for file_path, other_idx in conflicts:
                    task = await dag.get_task(task_id)
                    logger.warning(
                        "Conflict: employee %d and %d both editing %s",
                        activity.employee_index, other_idx, file_path,
                    )
                    post_conflict(config, file_path, activity.employee_index, other_idx, task.project_repo)
                    # Send guidance to the later employee
                    if task.workspace:
                        send_guidance(
                            task.workspace, activity.employee_index, "warning",
                            f"Employee {other_idx} is also editing {file_path}. "
                            f"Coordinate via separate functions or wait for them to finish.",
                        )
                        post_guidance(
                            config, activity.employee_index, "warning",
                            f"Conflict on {file_path} with employee {other_idx}",
                            task.project_repo,
                        )

    # Final summary
    summary = await dag.summary()
    logger.info("Scheduler complete: %s", summary)


async def _run_and_monitor(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
    activity: EmployeeActivity,
    stop_event: asyncio.Event,
    conflict_detector: ConflictDetector,
    semaphore: asyncio.Semaphore,
    dag: TaskDAG,
    rate_limit_tracker: RateLimitTracker | None = None,
) -> str:
    """Run an employee and monitor their stream. Returns task_id when done."""
    try:
        # Start employee subprocess
        employee_task = asyncio.create_task(
            run_employee(task, config, employee_index)
        )

        # Wait briefly for stream file to appear, then start monitoring
        stream_file = _get_stream_file(config, task.project_repo, employee_index)
        monitor_task = asyncio.create_task(
            monitor_stream(stream_file, task, activity, config, stop_event)
        )

        # Wait for employee to finish
        result: EmployeeResult = await employee_task
        exit_code = result.exit_code

        # Stop the monitor
        stop_event.set()
        try:
            await asyncio.wait_for(monitor_task, timeout=2.0)
        except asyncio.TimeoutError:
            monitor_task.cancel()

        # Record rate limit status with the tracker
        if rate_limit_tracker is not None:
            newly_exhausted = rate_limit_tracker.record_employee_result(
                employee_index=employee_index,
                task_id=task.id,
                rate_limited=result.rate_limited,
                rate_limit_reason=result.rate_limit_reason,
                exit_code=exit_code,
            )
            if newly_exhausted:
                # Budget just became exhausted — the scheduler's main loop
                # handles notification via the usage_check_counter path.
                # We can't reconstruct workspace info here, so we pass empty.
                handle_budget_exhaustion(config, dag, rate_limit_tracker, {})

        # Flush touched files to DB
        if activity.files_touched:
            await dag.update_touched_files(task.id, list(activity.files_touched))

        # Update DAG based on result
        if exit_code == 0:
            await dag.mark_completed(task.id, exit_code)
            post_task_event(config, "task_completed", task)
            logger.info("Task '%s' completed successfully (employee %d)", task.title, employee_index)
        elif result.rate_limited:
            error_msg = f"Rate limited: {result.rate_limit_reason}"
            await dag.mark_failed(task.id, error_msg, exit_code)
            post_task_event(config, "task_failed", task)
            logger.warning("Task '%s' rate-limited (employee %d): %s", task.title, employee_index, result.rate_limit_reason)
        else:
            await dag.mark_failed(task.id, f"Employee exited with code {exit_code}", exit_code)
            post_task_event(config, "task_failed", task)
            logger.warning("Task '%s' failed (employee %d, exit %d)", task.title, employee_index, exit_code)

        # Check if employee had too many test failures
        if activity.consecutive_test_failures >= config.max_consecutive_failures:
            logger.warning(
                "Employee %d had %d consecutive test failures",
                employee_index, activity.consecutive_test_failures,
            )

    except Exception as e:
        logger.exception("Error running task '%s'", task.title)
        await dag.mark_failed(task.id, str(e))
        post_task_event(config, "task_failed", task)

    finally:
        semaphore.release()

    return task.id


async def _setup_task_workspace(task: Task, config: CoordinatorConfig, employee_index: int) -> str | None:
    """Setup workspace for a task. Employee 0 uses main workspace, others get worktrees."""
    repo_name = task.project_repo.split("/")[-1] if "/" in task.project_repo else task.project_repo
    main_workspace = os.path.join(config.workspaces_dir, repo_name)

    if employee_index == 0:
        # Use main workspace — clone if it doesn't exist yet
        if not Path(main_workspace).exists():
            logger.info("Workspace missing, cloning %s into %s", task.project_repo, main_workspace)
            try:
                Path(config.workspaces_dir).mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    ["gh", "repo", "clone", task.project_repo, repo_name],
                    cwd=config.workspaces_dir,
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    logger.error("Failed to clone %s: %s", task.project_repo, result.stderr)
                    return None
                logger.info("Cloned %s successfully", task.project_repo)
            except Exception as e:
                logger.error("Clone failed for %s: %s", task.project_repo, e)
                return None
        return main_workspace

    # Create worktree for concurrent employees
    # Ensure main workspace exists first (worktrees branch from it)
    if not Path(main_workspace).exists():
        logger.info("Main workspace missing for worktree, cloning %s", task.project_repo)
        try:
            Path(config.workspaces_dir).mkdir(parents=True, exist_ok=True)
            clone_result = subprocess.run(
                ["gh", "repo", "clone", task.project_repo, repo_name],
                cwd=config.workspaces_dir,
                capture_output=True, text=True, timeout=120,
            )
            if clone_result.returncode != 0:
                logger.error("Failed to clone %s: %s", task.project_repo, clone_result.stderr)
                return None
        except Exception as e:
            logger.error("Clone failed for %s: %s", task.project_repo, e)
            return None

    worktree_dir = os.path.join(config.workspaces_dir, f"{repo_name}-e{employee_index}")
    branch_name = f"employee-{employee_index}-{config.run_id}"

    try:
        # Clean up any existing worktree at this path
        if Path(worktree_dir).exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_dir],
                cwd=main_workspace, capture_output=True, timeout=10,
            )

        # Create new worktree
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_dir, "HEAD"],
            cwd=main_workspace, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("Failed to create worktree: %s", result.stderr)
            return None

        logger.info("Created worktree for employee %d: %s", employee_index, worktree_dir)
        return worktree_dir

    except Exception as e:
        logger.error("Worktree setup failed: %s", e)
        return None


def _get_stream_file(config: CoordinatorConfig, project_repo: str, employee_index: int) -> str:
    """Get stream file path matching run-manager.sh conventions."""
    repo_name = project_repo.split("/")[-1] if "/" in project_repo else project_repo
    return os.path.join(
        config.log_dir,
        f"run-{config.run_id}-{repo_name}-e{employee_index}.stream.jsonl",
    )
