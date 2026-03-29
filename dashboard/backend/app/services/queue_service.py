"""Centralized queue state management.

All queue state transitions should go through this module to ensure
the TRANSITIONS state machine is enforced consistently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueueItem

logger = logging.getLogger(__name__)

# Valid state transitions (single source of truth)
TRANSITIONS: dict[str, set[str]] = {
    "pending":     {"assigned", "claimed", "planning", "paused", "failed", "cancelled"},
    "claimed":     {"in_progress", "pending", "paused"},
    "assigned":    {"in_progress", "pending", "paused", "failed", "cancelled"},
    "planning":    {"in_progress", "paused", "failed", "pending"},
    "in_progress": {"review", "verifying", "paused", "failed", "pending"},
    "verifying":   {"approved", "rejected", "pending"},
    "review":      {"approved", "rejected", "pending"},
    "approved":    {"completed"},
    "rejected":    {"pending", "failed", "escalated"},
    "escalated":   {"pending"},
    "paused":      {"pending"},
    "failed":      {"pending"},
    "cancelled":   set(),
}

ALL_STATES = set(TRANSITIONS.keys()) | {"completed"}
ACTIVE_STATES = {"pending", "claimed", "assigned", "planning", "in_progress", "review", "verifying"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def transition_state(item: QueueItem, new_state: str, force: bool = False) -> None:
    """Transition a queue item to a new state, enforcing the state machine.

    Args:
        item: The queue item to transition.
        new_state: The target state.
        force: If True, bypass state machine validation (for recovery scenarios).

    Raises:
        ValueError: If the transition is not allowed and force=False.
    """
    if new_state == item.state:
        return

    if not force:
        allowed = TRANSITIONS.get(item.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid state transition: {item.state} -> {new_state}. "
                f"Allowed: {sorted(allowed)}"
            )

    item.state = new_state

    now = _utcnow()
    if new_state in ("assigned", "claimed"):
        item.assigned_at = now
    elif new_state == "in_progress":
        item.started_at = now
    elif new_state == "completed":
        item.completed_at = now

    item.updated_at = now


async def reset_orphaned_item(
    item: QueueItem, reason: str = "stale run recovery"
) -> None:
    """Reset an orphaned queue item back to pending state.

    Uses force=True because orphaned items may be in states where
    pending is not normally reachable (e.g. in_progress -> pending).
    """
    old_state = item.state
    transition_state(item, "pending", force=True)
    item.run_id = None
    item.assigned_to = None
    logger.info(
        "Reset orphaned queue item %d from %s to pending (%s)",
        item.id, old_state, reason,
    )
