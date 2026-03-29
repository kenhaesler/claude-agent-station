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

    return ctask


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
