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

    Events: started, finished, verdict
    """
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

    if event.event == "started":
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

    elif event.event == "finished":
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

    elif event.event == "verdict":
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

    await db.commit()
    logger.info("Processed webhook event: %s for %s", event.event, event.run_id)
    return {"status": "ok", "run_id": event.run_id, "event": event.event}


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
