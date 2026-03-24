"""Active Foreman: real-time employee management during coordinated runs.

Runs alongside the scheduler as an async task. Periodically checks
employee activity for stuck patterns, file conflicts, budget issues,
and sends proactive guidance.

Inspired by Anthropic's multi-agent research: unchecked subagents waste
massive tokens on wrong approaches. Active monitoring with early
intervention prevents this.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import TaskDAG
    from agent.coordinator.stream_monitor import EmployeeActivity, ConflictDetector

from agent.coordinator.guidance import send_guidance
from agent.coordinator.reporter import post_guidance
from agent.coordinator.shared_context import write_team_context

logger = logging.getLogger(__name__)

# Default check interval in seconds
DEFAULT_CHECK_INTERVAL = 30.0

# Minimum seconds between guidance messages to the same employee
GUIDANCE_COOLDOWN = 90.0


async def run_foreman(
    config: CoordinatorConfig,
    dag: TaskDAG,
    activities: dict[str, EmployeeActivity],
    conflict_detector: ConflictDetector,
    task_workspaces: dict[str, str],
    stop_event: asyncio.Event,
) -> None:
    """Active work management loop.

    Runs until stop_event is set. Checks employee activity periodically
    and sends guidance when problems are detected.
    """
    check_interval = DEFAULT_CHECK_INTERVAL
    guidance_cooldown: dict[int, float] = {}

    logger.info("Foreman started (check interval: %.0fs)", check_interval)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=check_interval,
            )
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # Normal timeout, run checks

        if not activities:
            continue

        # Write team context for employee awareness
        _write_context(activities, task_workspaces)

        # Check each active employee
        for task_id, activity in list(activities.items()):
            employee = activity.employee_index
            workspace = task_workspaces.get(task_id, "")
            if not workspace:
                continue

            # Rate-limit guidance per employee
            if _in_cooldown(employee, guidance_cooldown):
                continue

            # Check 1: Stuck detection
            if activity.is_stuck:
                send_guidance(
                    workspace, employee, "redirect",
                    f"You appear to be stuck: {activity.stuck_reason}. "
                    f"Step back, reconsider your approach, and try a different strategy.",
                )
                _mark_cooldown(employee, guidance_cooldown)
                post_guidance(
                    config, employee, "redirect",
                    activity.stuck_reason,
                    dag.project_repo,
                )
                activity.is_stuck = False  # Reset after guidance
                logger.info(
                    "Foreman: sent redirect to employee %d: %s",
                    employee, activity.stuck_reason,
                )
                continue

            # Check 2: File conflicts with other employees
            conflicts = conflict_detector.check_conflicts(employee)
            if conflicts:
                conflict_files = [f for f, _ in conflicts[:3]]
                send_guidance(
                    workspace, employee, "warning",
                    f"Another employee is also editing: {', '.join(conflict_files)}. "
                    f"Avoid modifying these files to prevent merge conflicts.",
                )
                _mark_cooldown(employee, guidance_cooldown)
                post_guidance(
                    config, employee, "warning",
                    f"File conflict: {', '.join(conflict_files[:2])}",
                    dag.project_repo,
                )
                continue

            # Check 3: Approaching turn budget (use mode-resolved max_turns)
            effective_max = activity.max_turns or config.max_employee_turns
            budget_pct = activity.tool_calls / max(1, effective_max)
            if budget_pct > 0.8 and not getattr(activity, '_budget_warned', False):
                send_guidance(
                    workspace, employee, "info",
                    f"You've used {activity.tool_calls} of ~{effective_max} turns. "
                    f"Start wrapping up: commit your work and write your report.",
                )
                _mark_cooldown(employee, guidance_cooldown)
                activity._budget_warned = True  # type: ignore[attr-defined]
                logger.info(
                    "Foreman: budget warning to employee %d (%d/%d turns)",
                    employee, activity.tool_calls, config.max_employee_turns,
                )

    logger.info("Foreman stopped")


def _in_cooldown(employee: int, cooldowns: dict[int, float]) -> bool:
    """Check if an employee is in guidance cooldown."""
    last = cooldowns.get(employee, 0)
    return (time.monotonic() - last) < GUIDANCE_COOLDOWN


def _mark_cooldown(employee: int, cooldowns: dict[int, float]) -> None:
    """Mark an employee as having received guidance."""
    cooldowns[employee] = time.monotonic()


def _write_context(
    activities: dict[str, EmployeeActivity],
    task_workspaces: dict[str, str],
) -> None:
    """Write team context to all active workspaces."""
    if not activities:
        return

    # Collect all unique workspaces
    workspaces = set(task_workspaces.values())

    for workspace in workspaces:
        try:
            write_team_context(workspace, activities)
        except Exception as e:
            logger.debug("Failed to write team context to %s: %s", workspace, e)
