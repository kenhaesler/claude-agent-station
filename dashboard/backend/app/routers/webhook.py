"""Webhook endpoint for receiving live run events from run-manager.sh."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Run, Project, Notification
from app.schemas import WebhookRunEvent
from app.services.event_bus import publish as event_bus_publish

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
                started_at=datetime.now(timezone.utc),
                employee_index=event.employee_index,
                concurrent_group_id=event.concurrent_group_id,
            )
            db.add(run)
        else:
            run.status = "running"
            run.project_id = project_id or run.project_id
            run.mode = event.mode or run.mode
            run.model = event.model or run.model
            if event.employee_index is not None:
                run.employee_index = event.employee_index
            if event.concurrent_group_id:
                run.concurrent_group_id = event.concurrent_group_id

    elif event_name == "finished":
        # Normalize status: run-manager.sh sends "success"/"no_reports",
        # but the frontend expects "completed"/"failed" for styling.
        raw_status = event.status or "finished"
        status_map = {"success": "completed", "finished": "completed"}
        final_status = status_map.get(raw_status, raw_status)

        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                status=final_status,
            )
            db.add(run)

        run.status = final_status
        run.cost_usd = event.cost_usd
        run.tokens_input = event.tokens_input
        run.tokens_output = event.tokens_output
        run.tokens_total = event.tokens_total
        run.turns = event.turns
        run.duration_ms = event.duration_ms
        run.finished_at = datetime.now(timezone.utc)
        run.model = event.model or run.model

    elif event_name == "employee_done":
        # Employee finished working — update employee-specific data but keep
        # run status as "running" so the dashboard doesn't show it as complete
        # before the manager review phase.
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
        # Keep status as "running" — do NOT set to finished
        if event.mode:
            run.mode = event.mode
        if event.model:
            run.model = event.model
        if project_id:
            run.project_id = project_id

    elif event_name == "reviewing":
        # Manager review phase — transition to a meaningful intermediate status
        if not run:
            run = Run(
                run_id=event.run_id,
                project_id=project_id,
                status="reviewing",
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
        else:
            run.status = "reviewing"

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
                started_at=datetime.now(timezone.utc),
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

    # Broadcast to SSE subscribers for real-time dashboard updates
    await event_bus_publish({
        "type": event.event,
        "data": {
            "run_id": event.run_id,
            "event": event.event,
            "normalized": event_name,
            "project": event.project,
            "status": event.status,
            "verdict": event.verdict,
            "issue_number": event.issue_number,
            "branch": event.branch,
            "mode": event.mode,
            "model": event.model,
            "employee_index": event.employee_index,
            "concurrent_group_id": event.concurrent_group_id,
        },
    })

    return {"status": "ok", "run_id": event.run_id, "event": event.event}


def _normalize_event_name(event_name: str) -> str:
    """Map run-manager.sh event names to internal handler names.

    run-manager.sh sends: run_start, employee_start, employee_complete,
    manager_review, verdict_execute, run_complete.

    The handler expects: started, employee_done, reviewing, verdict, finished.

    Previously employee_complete mapped to "finished" which prematurely marked
    runs as done before the manager review phase. Now only run_complete triggers
    the "finished" handler.
    """
    mapping = {
        "run_start": "started",
        "employee_start": "started",
        "employee_complete": "employee_done",
        "manager_review": "reviewing",
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
