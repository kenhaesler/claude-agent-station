"""Coordinator task and message management -- extracted from webhook.py.

Handles DAG task lifecycle events (started/completed/failed/ready/blocked)
and coordinator messages (conflict detection, guidance).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoordinatorMessage, CoordinatorTask
from app.schemas import WebhookRunEvent

logger = logging.getLogger(__name__)


async def handle_task_event(
    db: AsyncSession, event: WebhookRunEvent, event_name: str
) -> CoordinatorTask | None:
    """Upsert a CoordinatorTask based on a task lifecycle event."""
    if not event.task_id:
        return None

    result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.id == event.task_id)
    )
    ctask = result.scalar_one_or_none()
    if not ctask:
        ctask = CoordinatorTask(
            id=event.task_id,
            run_id=event.run_id,
            project_repo=event.project or "",
            title=event.task_title or "",
            depends_on=event.depends_on,
            employee_index=event.employee_index,
        )
        db.add(ctask)

    status_map = {
        "task_started": "running",
        "task_completed": "completed",
        "task_failed": "failed",
        "task_ready": "ready",
        "task_blocked": "blocked",
    }
    ctask.status = status_map.get(event_name, ctask.status)
    if event_name == "task_started":
        ctask.started_at = datetime.now(timezone.utc)
        ctask.employee_index = event.employee_index
    elif event_name in ("task_completed", "task_failed"):
        ctask.finished_at = datetime.now(timezone.utc)

    # Populate Agent Teams identity fields
    if event.agent_name:
        ctask.claimed_by = event.agent_name
    if event.agent_id:
        ctask.teammate_agent_id = event.agent_id

    # Issue #336: capture the final teammate progress snapshot when the
    # task closes (the orchestrator includes it on teammate_completed).
    # Monotonic max guards against a late progress event arriving after
    # completion with a stale snapshot.
    if event.tokens_total is not None:
        ctask.tokens_total = max(ctask.tokens_total or 0, event.tokens_total)
    if event.turns is not None:
        ctask.turns = max(ctask.turns or 0, event.turns)

    return ctask


async def handle_teammate_progress(
    db: AsyncSession, event: WebhookRunEvent
) -> None:
    """Update CoordinatorTask with latest tool activity and log a progress message."""
    if not event.task_id:
        return

    # Update task's result_summary with latest tool name
    result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.id == event.task_id)
    )
    ctask = result.scalar_one_or_none()
    if ctask:
        tool_name = event.agent_name or "unknown"
        ctask.result_summary = f"Using {tool_name}"
        # Issue #336: write per-teammate progress to dedicated columns.
        # Earlier revisions stuffed a {"tokens": ..., "turns": ...} dict into
        # ``touched_files`` (which is reserved for the JSON file array), and
        # the Fleet page never read it back. Treat counters as monotonic so a
        # late event with a stale snapshot can't roll the cell backwards.
        if event.tokens_total is not None:
            ctask.tokens_total = max(ctask.tokens_total or 0, event.tokens_total)
        if event.turns is not None:
            ctask.turns = max(ctask.turns or 0, event.turns)

    # Create a progress message for the activity feed
    msg = CoordinatorMessage(
        run_id=event.run_id,
        task_id=event.task_id,
        direction="from_employee",
        message_type="progress",
        content=json.dumps({
            "tool": event.agent_name or "unknown",
            "turns": event.turns,
            "tokens": event.tokens_total,
        }),
    )
    db.add(msg)


async def handle_coordinator_message(
    db: AsyncSession, event: WebhookRunEvent, event_name: str
) -> CoordinatorMessage:
    """Create a coordinator message (conflict or guidance)."""
    msg = CoordinatorMessage(
        run_id=event.run_id,
        task_id=event.task_id,
        direction="from_monitor" if event_name == "conflict_detected" else "to_employee",
        message_type="conflict" if event_name == "conflict_detected" else "guidance",
        content=json.dumps({
            "file_path": event.file_path,
            "employee_a": event.employee_a,
            "employee_b": event.employee_b,
            "guidance_type": event.guidance_type,
            "guidance_content": event.guidance_content,
        }),
        employee_index=event.employee_index,
    )
    db.add(msg)
    return msg
