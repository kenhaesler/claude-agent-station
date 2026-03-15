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
from agent.coordinator.employee_runner import (
    run_employee,
    run_employee_plan_phase,
    _run_claude_subprocess,
    _get_stream_file as _employee_get_stream_file,
    EmployeeResult,
    PROMPTS_DIR,
)
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


def _should_skip_planning(task: Task, config: CoordinatorConfig) -> bool:
    """Check if planning phase should be skipped for this task."""
    if not config.planning_enabled:
        return True

    # Read-only and lightweight modes don't need a plan-before-implement phase
    if config.project_mode in ("analyze", "plan", "triage", "review", "fix"):
        return True

    # Check for skip-planning label in assignment file
    assignment_file = Path(task.workspace) / f".claude-assignment-{task.employee_index}.json"
    if assignment_file.exists():
        try:
            assignment = json.loads(assignment_file.read_text())
            labels = assignment.get("labels", [])
            # Labels may be strings or dicts with a "name" key
            label_names = [
                l.get("name", "") if isinstance(l, dict) else str(l)
                for l in labels
            ]
            if "skip-planning" in label_names:
                return True
        except (json.JSONDecodeError, OSError):
            pass

    return False


async def _run_manager_plan_review(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
    plan: dict,
) -> tuple[str, str]:
    """Run manager agent to review an employee's plan.

    Returns (verdict, feedback) where verdict is one of:
    APPROVE_PLAN, REVISE_PLAN, REJECT_PLAN.
    """
    plan_json = json.dumps(plan, indent=2)
    review_file = Path(config.log_dir) / f"run-{config.run_id}-plan-review-e{employee_index}.md"
    verdict_file = Path(config.log_dir) / f"run-{config.run_id}-plan-verdict-e{employee_index}.json"

    # Build plan review package
    review_content = f"""# Plan Review Package - Employee {employee_index}

## MODE: PLAN_REVIEW

Review the implementation plan below. This is NOT code review -- it is plan review before implementation.

## Project: {task.project_repo}
## Task: {task.title}
## Description: {task.description}

## Employee's Implementation Plan:
```json
{plan_json}
```

Write your plan verdict to: {verdict_file}
"""
    review_file.write_text(review_content)

    # Run manager for plan review
    manager_model = "claude-sonnet-4-6"
    # Inline review content directly to avoid wasting turns re-reading the file
    manager_prompt = (
        f"Review the employee's implementation plan below.\n\n"
        f"Write your plan verdict to: {verdict_file}\n\n"
        "Use APPROVE_PLAN if the plan is solid, REVISE_PLAN with specific feedback "
        "if it needs changes, or REJECT_PLAN if the plan is fundamentally flawed.\n\n"
        "--- BEGIN PLAN REVIEW PACKAGE ---\n"
        f"{review_content}\n"
        "--- END PLAN REVIEW PACKAGE ---"
    )

    stream_file = str(
        Path(config.log_dir) / f"run-{config.run_id}-plan-review-e{employee_index}.stream.jsonl"
    )

    env = os.environ.copy()
    env["GITHUB_REPO"] = task.project_repo

    # Use shared subprocess helper (also adds rate limit detection that was
    # previously missing from manager plan review)
    manager_fallback = (
        "claude-haiku-4-5-20251001"
        if manager_model != "claude-haiku-4-5-20251001"
        else "claude-sonnet-4-6"
    )

    result = await _run_claude_subprocess(
        prompt=manager_prompt,
        system_prompt_file=str(PROMPTS_DIR / "manager.md"),
        model=manager_model,
        fallback_model=manager_fallback,
        max_turns=5,
        stream_file=stream_file,
        cwd=task.workspace,
        env=env,
        label=f"manager-plan-review-e{employee_index}",
    )

    if result.rate_limited:
        logger.warning(
            "Manager plan review rate limited for employee %d: %s",
            employee_index, result.rate_limit_reason,
        )

    # Parse verdict file
    if verdict_file.exists():
        try:
            verdict_data = json.loads(verdict_file.read_text())
            verdicts = verdict_data.get("plan_verdicts", [])
            if verdicts:
                v = verdicts[0]
                return v.get("verdict", "APPROVE_PLAN"), v.get("feedback", "")
        except (json.JSONDecodeError, OSError):
            pass

    # Default to approve if manager didn't produce parseable verdict
    logger.warning("Could not parse plan verdict for employee %d, defaulting to APPROVE_PLAN", employee_index)
    return "APPROVE_PLAN", ""


async def _run_plan_review_loop(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
) -> dict | None:
    """Run the plan-create -> manager-review -> revise loop.

    Returns the approved plan dict, or None if planning failed/was rejected.
    """
    max_revisions = config.planning_max_revisions
    revision_feedback: str | None = None

    for attempt in range(max_revisions + 1):  # initial + N revisions
        # Run employee in plan-only mode
        result = await run_employee_plan_phase(
            task,
            config,
            employee_index,
            revision_feedback=revision_feedback,
        )

        if result.exit_code != 0 or result.rate_limited:
            logger.warning(
                "Employee %d plan phase failed (exit=%d, rate_limited=%s)",
                employee_index,
                result.exit_code,
                result.rate_limited,
            )
            return None

        # Read the plan file
        plan_file = Path(task.workspace) / f".claude-employee-plan-{employee_index}.json"
        if not plan_file.exists():
            logger.warning("Employee %d did not produce a plan file", employee_index)
            return None

        try:
            plan = json.loads(plan_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read plan file for employee %d: %s", employee_index, e)
            return None

        # Run manager plan review
        verdict, feedback = await _run_manager_plan_review(
            task,
            config,
            employee_index,
            plan,
        )

        if verdict == "APPROVE_PLAN":
            logger.info("Plan APPROVED for employee %d", employee_index)
            # Write approved plan for reference during implementation
            approved_file = Path(task.workspace) / f".claude-approved-plan-{employee_index}.json"
            approved_file.write_text(json.dumps(plan, indent=2))
            return plan

        elif verdict == "REJECT_PLAN":
            logger.warning("Plan REJECTED for employee %d: %s", employee_index, feedback)
            return None

        elif verdict == "REVISE_PLAN":
            if attempt < max_revisions:
                logger.info(
                    "Plan needs revision for employee %d (attempt %d/%d): %s",
                    employee_index,
                    attempt + 1,
                    max_revisions,
                    feedback[:200],
                )
                revision_feedback = feedback
            else:
                logger.warning(
                    "Max plan revisions (%d) reached for employee %d, auto-approving",
                    max_revisions,
                    employee_index,
                )
                approved_file = Path(task.workspace) / f".claude-approved-plan-{employee_index}.json"
                approved_file.write_text(json.dumps(plan, indent=2))
                return plan

    return None


async def _is_issue_open(project_repo: str, issue_number: int) -> bool:
    """Check if a GitHub issue is still open. Returns True if open or on error (fail-open)."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["gh", "issue", "view", str(issue_number), "--repo", project_repo, "--json", "state"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            state = data.get("state", "OPEN").upper()
            return state == "OPEN"
    except Exception as e:
        logger.warning("Failed to check issue #%d state (fail-open): %s", issue_number, e)
    # Fail-open: if we can't determine state, allow work to proceed
    return True


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

            # Freshness check: verify issue is still open before spawning (#139)
            if task.issue_number:
                if not await _is_issue_open(task.project_repo, task.issue_number):
                    logger.warning(
                        "Skipping task '%s': issue #%d is no longer open",
                        task.title, task.issue_number,
                    )
                    await dag.mark_failed(
                        task.id,
                        f"Issue #{task.issue_number} was closed before employee could start",
                    )
                    post_task_event(config, "task_failed", task)
                    continue

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
            # Also send employee_start to create a Run record for the dashboard
            from agent.coordinator.reporter import post_employee_start
            post_employee_start(config, task)

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
                    actual_running=len(running) + 1,
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
    actual_running: int = 1,
) -> str:
    """Run an employee and monitor their stream. Returns task_id when done."""
    try:
        # --- Plan review gate ---
        approved_plan: dict | None = None
        if config.project_mode != "analyze" and not _should_skip_planning(task, config):
            logger.info(
                "Starting plan phase for employee %d, task '%s'",
                employee_index,
                task.title,
            )
            approved_plan = await _run_plan_review_loop(task, config, employee_index)
            if approved_plan is None:
                logger.warning(
                    "Plan phase failed for employee %d, marking task failed",
                    employee_index,
                )
                await dag.mark_failed(task.id, "Plan phase failed or was rejected")
                post_task_event(config, "task_failed", task)
                return task.id

        # Start employee subprocess (with approved plan if available)
        # Note: analyze/plan mode enforcement is handled by employee_runner.py
        # which selects analyst.md prompt and disallows write tools.
        employee_task = asyncio.create_task(
            run_employee(task, config, employee_index, approved_plan=approved_plan, actual_running=actual_running)
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
        # Send employee_complete to update the Run record in the dashboard
        from agent.coordinator.reporter import post_employee_complete
        exit_code_final = result.exit_code if result else 1
        post_employee_complete(config, task, exit_code_final)

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
    """Get stream file path matching run-manager.sh conventions.

    Delegates to employee_runner._get_stream_file for the canonical implementation.
    """
    return _employee_get_stream_file(config, project_repo, employee_index)
