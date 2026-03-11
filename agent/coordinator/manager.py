"""Plan usage awareness for the coordinator/manager.

Provides functions to check plan usage before spawning new employees
and to request graceful wrap-up when approaching usage thresholds.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import TaskDAG

# Add parent paths so detect_plan_usage can be imported
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from detect_plan_usage import (
    PlanUsageSnapshot,
    detect_plan_usage,
    save_usage_snapshot,
    should_throttle_spawning,
)
from agent.coordinator.guidance import send_guidance

logger = logging.getLogger(__name__)

# Default threshold: start throttling at 85% plan usage
DEFAULT_MAX_USAGE_PERCENT = 85.0

# At this percentage, send wrap-up signals to running employees
WRAP_UP_USAGE_PERCENT = 90.0

# At this percentage, refuse to spawn any new employees
HARD_STOP_USAGE_PERCENT = 95.0


def check_plan_usage_before_spawn(
    config: CoordinatorConfig,
    max_usage_percent: float = DEFAULT_MAX_USAGE_PERCENT,
) -> tuple[bool, str, PlanUsageSnapshot]:
    """Check plan usage and decide whether to allow spawning a new employee.

    Returns:
        (can_spawn, reason, snapshot):
            - can_spawn: True if usage is below threshold
            - reason: Human-readable explanation if denied
            - snapshot: The usage snapshot for further inspection
    """
    snapshot = detect_plan_usage(
        db_path=config.db_path,
        plan_tier=_get_plan_tier(config),
    )

    # Save snapshot for history tracking
    try:
        save_usage_snapshot(snapshot, config.db_path)
    except Exception as e:
        logger.warning("Failed to save usage snapshot: %s", e)

    # Hard stop — never spawn above this
    if snapshot.weekly_usage_percent >= HARD_STOP_USAGE_PERCENT or snapshot.is_throttled:
        reason = (
            f"Plan usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(hard stop at {HARD_STOP_USAGE_PERCENT:.1f}%). "
            f"No new employees will be spawned."
        )
        logger.warning("HARD STOP: %s", reason)
        return False, reason, snapshot

    # Throttle check
    throttle, throttle_reason = should_throttle_spawning(snapshot, max_usage_percent)
    if throttle:
        logger.warning("THROTTLE: %s", throttle_reason)
        return False, throttle_reason, snapshot

    logger.info(
        "Plan usage OK: %.1f%% weekly (threshold: %.1f%%)",
        snapshot.weekly_usage_percent, max_usage_percent,
    )
    return True, "", snapshot


def request_graceful_wrapup(
    config: CoordinatorConfig,
    dag: TaskDAG,
    snapshot: PlanUsageSnapshot,
    running_tasks: dict[str, int],  # task_id -> employee_index
) -> int:
    """Send graceful wrap-up guidance to all running employees.

    Called when usage is approaching the threshold but employees are
    already running. Asks them to finish current work, commit, and
    write their report.

    Args:
        config: Coordinator configuration
        dag: The task DAG
        snapshot: Current usage snapshot
        running_tasks: Map of task_id to employee_index for running tasks

    Returns:
        Number of employees notified
    """
    notified = 0

    for task_id, employee_index in running_tasks.items():
        task = dag.tasks.get(task_id)
        if not task or not task.workspace:
            continue

        message = (
            f"Plan usage is at {snapshot.weekly_usage_percent:.1f}% "
            f"(approaching limit). Please wrap up your current work: "
            f"commit what you have, write your report, and finish. "
            f"Do not start any new major changes."
        )

        try:
            send_guidance(
                workspace=task.workspace,
                employee_index=employee_index,
                guidance_type="warning",
                content=message,
            )
            notified += 1
            logger.info(
                "Sent wrap-up guidance to employee %d (task %s)",
                employee_index, task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to send wrap-up guidance to employee %d: %s",
                employee_index, e,
            )

    # If usage is critically high, send stop signals
    if snapshot.weekly_usage_percent >= HARD_STOP_USAGE_PERCENT:
        for task_id, employee_index in running_tasks.items():
            task = dag.tasks.get(task_id)
            if not task or not task.workspace:
                continue

            try:
                send_guidance(
                    workspace=task.workspace,
                    employee_index=employee_index,
                    guidance_type="stop",
                    content=(
                        f"CRITICAL: Plan usage at {snapshot.weekly_usage_percent:.1f}%. "
                        f"Stop work immediately. Commit what you have and write a partial report."
                    ),
                )
                logger.warning(
                    "Sent STOP guidance to employee %d (task %s) — critical usage",
                    employee_index, task_id,
                )
            except Exception as e:
                logger.error(
                    "Failed to send stop guidance to employee %d: %s",
                    employee_index, e,
                )

    return notified


def get_spawn_recommendation(
    config: CoordinatorConfig,
    desired_count: int,
    max_usage_percent: float = DEFAULT_MAX_USAGE_PERCENT,
) -> tuple[int, PlanUsageSnapshot]:
    """Get recommendation for how many employees to spawn.

    Based on current usage, may recommend fewer employees than desired
    to conserve plan budget.

    Args:
        config: Coordinator configuration
        desired_count: How many employees are requested
        max_usage_percent: Usage threshold

    Returns:
        (recommended_count, snapshot)
    """
    snapshot = detect_plan_usage(
        db_path=config.db_path,
        plan_tier=_get_plan_tier(config),
    )

    if snapshot.weekly_usage_percent >= HARD_STOP_USAGE_PERCENT or snapshot.is_throttled:
        return 0, snapshot

    if snapshot.weekly_usage_percent >= WRAP_UP_USAGE_PERCENT:
        # Allow at most 1 employee when near limit
        return min(1, desired_count), snapshot

    if snapshot.weekly_usage_percent >= max_usage_percent:
        # Reduce to half
        reduced = max(1, desired_count // 2)
        logger.info(
            "Reducing employee count from %d to %d (usage at %.1f%%)",
            desired_count, reduced, snapshot.weekly_usage_percent,
        )
        return reduced, snapshot

    # Usage is fine, allow full spawn
    return desired_count, snapshot


def _get_plan_tier(config: CoordinatorConfig) -> str:
    """Determine plan tier from config.

    Checks for a plan_tier setting in the coordinator config,
    falling back to 'max_5x' as default.
    """
    return getattr(config, "plan_tier", "max_5x")
