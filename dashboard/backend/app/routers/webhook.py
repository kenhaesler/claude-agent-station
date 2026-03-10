"""Webhook endpoint for receiving live run events from run-manager.sh."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Run, Project, Notification
from app.schemas import WebhookRunEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.post("/run-event")
async def receive_run_event(
    event: WebhookRunEvent,
    db: AsyncSession = Depends(get_db),
):
    """Receive a run event from the agent's run-manager.sh script.

    Events from run-manager.sh:
      run_start, employee_start, employee_complete, manager_review,
      verdict_execute, run_complete
    Also accepts legacy short names:
      started, finished, verdict
    """
    # Normalize event names from run-manager.sh to internal names
    event_name = _normalize_event_name(event.event)

    # Find or create Run record
    result = await db.execute(select(Run).where(Run.run_id == event.run_id))
    run = result.scalar_one_or_none()

    # Try to match project
    project_id = None
    if event.project:
        proj_result = await db.execute(
            select(Project).where(Project.repo == event.project)
        )
        proj = proj_result.scalar_one_or_none()
        if not proj:
            # Try matching by short name
            short = event.project.split("/")[-1] if "/" in event.project else event.project
            proj_result = await db.execute(select(Project))
            for p in proj_result.scalars().all():
                p_short = p.repo.split("/")[-1] if "/" in p.repo else p.repo
                if p_short == short:
                    proj = p
                    break
        if proj:
            project_id = proj.id

    if event_name == "started":
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                mode=event.mode,
                model=event.model,
                status="running",
                started_at=datetime.utcnow(),
            )
            db.add(run)
        else:
            run.status = "running"
            run.project_id = project_id or run.project_id
            run.mode = event.mode or run.mode
            run.model = event.model or run.model

    elif event_name == "finished":
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                status=event.status or "finished",
            )
            db.add(run)

        run.status = event.status or "finished"
        run.cost_usd = event.cost_usd
        run.turns = event.turns
        run.duration_ms = event.duration_ms
        run.finished_at = datetime.utcnow()
        run.model = event.model or run.model

    elif event_name == "verdict":
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
            )
            db.add(run)

        run.verdict = event.verdict
        run.issue_number = event.issue_number
        run.branch = event.branch
        if event.reasoning:
            run.verdict_detail = json.dumps({
                "verdict": event.verdict,
                "reasoning": event.reasoning,
                "issue_number": event.issue_number,
                "branch": event.branch,
            })

        # Create notification
        notification = Notification(
            run_id=event.run_id,
            type=event.verdict.lower() if event.verdict else "info",
            message=_build_notification_message(event),
        )
        db.add(notification)

    else:
        # Unknown event — still log it, but create/update the run record
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                status=event.status or "running",
                started_at=datetime.utcnow(),
            )
            db.add(run)
        # Update fields if provided
        if event.mode:
            run.mode = event.mode
        if event.model:
            run.model = event.model
        if event.status:
            run.status = event.status
        if project_id:
            run.project_id = project_id

    await db.commit()
    logger.info("Processed webhook event: %s (normalized: %s) for %s", event.event, event_name, event.run_id)
    return {"status": "ok", "run_id": event.run_id, "event": event.event}


def _normalize_event_name(event_name: str) -> str:
    """Map run-manager.sh event names to internal handler names.

    run-manager.sh sends: run_start, employee_start, employee_complete,
    manager_review, verdict_execute, run_complete.
    The handler expects: started, finished, verdict.
    """
    mapping = {
        "run_start": "started",
        "employee_start": "started",
        "employee_complete": "finished",
        "run_complete": "finished",
        "verdict_execute": "verdict",
        # Legacy / direct names pass through
        "started": "started",
        "finished": "finished",
        "verdict": "verdict",
    }
    return mapping.get(event_name, event_name)


def _build_notification_message(event: WebhookRunEvent) -> str:
    """Build a human-readable notification message."""
    project = event.project or "unknown"
    if event.verdict == "APPROVE":
        return f"[{project}] Changes approved and pushed"
    elif event.verdict == "PR":
        return f"[{project}] PR created for issue #{event.issue_number}"
    elif event.verdict == "REJECT":
        reason = (event.reasoning or "")[:100]
        return f"[{project}] Changes rejected: {reason}"
    else:
        return f"[{project}] {event.event}: {event.verdict or event.status or ''}"
