"""Webhook endpoint for receiving live run events from run-manager.sh.

This is a thin routing layer. Business logic is in:
  - services/run_lifecycle.py (run state management)
  - services/coordinator_service.py (task/message handling)
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import AgentEvent, Project, Run
from app.schemas import WebhookRunEvent
from app.services.event_bus import publish as event_bus_publish
from app.services.idempotency import is_duplicate
from app.services.notifier import send_notification
from app.services import run_lifecycle, coordinator_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


# Event name -> handler function mapping
_RUN_HANDLERS = {
    "started": run_lifecycle.handle_started,
    "finished": run_lifecycle.handle_finished,
    "employee_done": run_lifecycle.handle_employee_done,
    "reviewing": run_lifecycle.handle_reviewing,
    "plan_reviewing": run_lifecycle.handle_plan_reviewing,
    "plan_review_done": run_lifecycle.handle_plan_review_done,
    # Plan-review gate (issue #266) — emitted by agent.plan_review_gate
    # via the post-manager-review hook in run-manager.sh.
    "awaiting_plan_review": run_lifecycle.handle_awaiting_plan_review,
    "plan_approved": run_lifecycle.handle_plan_approved,
    "plan_rejected": run_lifecycle.handle_plan_rejected,
}

_TASK_EVENTS = {"task_started", "task_completed", "task_failed", "task_ready", "task_blocked"}
_MESSAGE_EVENTS = {"conflict_detected", "guidance_sent"}
_DAG_EVENTS = {"dag_created", "dag_completed"}
_TEAM_SPAWN_EVENTS = {"teammate_spawned", "team_created"}


@router.post("/run-event")
async def receive_run_event(
    event: WebhookRunEvent,
    db: AsyncSession = Depends(get_db),
    x_webhook_token: str | None = Header(None, alias="X-Webhook-Token"),
):
    """Receive a run event from the agent's run-manager.sh script."""
    # Auth
    if settings.webhook_secret and (
        not x_webhook_token
        or not secrets.compare_digest(x_webhook_token, settings.webhook_secret)
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")

    # Auto-generate trace and event IDs
    if not event.event_id:
        event.event_id = f"evt-{uuid.uuid4().hex[:12]}"
    if not event.trace_id:
        event.trace_id = f"trace-{event.run_id}"

    # Deduplicate
    if is_duplicate(event.event_id):
        logger.info("Skipping duplicate event: %s", event.event_id)
        return {"status": "duplicate", "run_id": event.run_id, "event_id": event.event_id}

    event_name = _normalize_event_name(event.event)

    # Find existing run
    result = await db.execute(select(Run).where(Run.run_id == event.run_id))
    run = result.scalar_one_or_none()

    # Heartbeat: any event for a known run row bumps last_event_at.
    # NULL persists if the run row doesn't exist yet (e.g. orchestrator
    # is mid-spawn). See issue #348.
    if run is not None:
        run.last_event_at = datetime.now(timezone.utc)

    # Resolve project
    project_id = await _resolve_project_id(db, event.project)

    # ---- Dispatch to appropriate handler ----

    if event_name == "verdict":
        run, _ = await run_lifecycle.handle_verdict(db, event, project_id, run)

    elif event_name in _RUN_HANDLERS:
        run = await _RUN_HANDLERS[event_name](db, event, project_id, run)

    elif event_name in _TASK_EVENTS:
        await coordinator_service.handle_task_event(db, event, event_name)

    elif event_name in _MESSAGE_EVENTS:
        await coordinator_service.handle_coordinator_message(db, event, event_name)

    elif event_name in _DAG_EVENTS:
        pass  # DAG is saved as a file by the coordinator

    elif event_name == "vision_misalignment":
        db.add(AgentEvent(
            workflow_id=f"trace-{event.run_id}",
            run_id=event.run_id,
            agent_id=event.agent_id or "lead",
            event_type="vision_misalignment",
            event_data=json.dumps({
                "issue_number": event.issue_number,
                "violated_section": event.violated_section,
                "quote": event.quote,
                "plan_excerpt": event.plan_excerpt,
            }),
        ))

    elif event_name == "hook_failures":
        # Posted by the orchestrator when the bundled CLI's hook callback
        # to Python fails mid-run (see agent/audit_hook.py). Persist as an
        # AgentEvent so dashboard queries can count affected runs without
        # operators grepping launcher.out by hand.
        db.add(AgentEvent(
            workflow_id=f"trace-{event.run_id}",
            run_id=event.run_id,
            agent_id=event.agent_id or "lead",
            event_type="hook_callback_failure",
            event_data=json.dumps({
                "project": event.project,
                "count": event.count,
            }),
        ))

    elif event_name in ("conflict_resolution_started",
                        "conflict_resolution_phase",
                        "conflict_resolution_completed"):
        db.add(AgentEvent(
            workflow_id=f"trace-{event.run_id}",
            run_id=event.run_id,
            agent_id=event.agent_id or "conflict-resolver",
            event_type=event_name,
            event_data=json.dumps({
                "project": event.project,
                "branch": event.branch,
                "phase": event.phase,
                "count": event.count,
            }),
        ))

    else:
        run = await run_lifecycle.handle_unknown(db, event, project_id, run)

    # ---- Post-dispatch: Agent Teams updates ----

    if run and event_name == "progress_update":
        await run_lifecycle.handle_progress_update(run, event)

    if run and event_name == "teammate_completed":
        await run_lifecycle.handle_teammate_completed(run, event)
        if event.task_id:
            mapped = "task_completed" if event.status != "error" else "task_failed"
            await coordinator_service.handle_task_event(db, event, mapped)

    if run and event_name in _TEAM_SPAWN_EVENTS:
        await run_lifecycle.handle_team_member_spawn(run, event)
        if event_name == "teammate_spawned" and event.task_id:
            event_copy = event.model_copy()
            event_copy.task_title = event.agent_name or f"Teammate {event.task_id}"
            await coordinator_service.handle_task_event(db, event_copy, "task_started")

    if run and event_name == "teammate_progress" and event.task_id:
        await coordinator_service.handle_teammate_progress(db, event)

    # Agent Teams: update team name
    if run and event.team_name:
        run.team_name = event.team_name

    await db.commit()
    logger.info("Processed webhook event: %s (normalized: %s) for %s", event.event, event_name, event.run_id)

    # ---- SSE broadcast ----
    await event_bus_publish({
        "type": event.event,
        "event_id": event.event_id,
        "trace_id": event.trace_id,
        "sequence": event.sequence,
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
            "team_name": event.team_name,
            "agent_id": event.agent_id,
            "agent_name": event.agent_name,
            "tokens_input": event.tokens_input,
            "tokens_output": event.tokens_output,
            "tokens_total": event.tokens_total,
            "turns": event.turns,
            "narration": event.narration,
            "narration_kind": event.narration_kind,
            "vision_bootstrap_count": event.vision_bootstrap_count,
            "vision_bootstrap_proposals": event.vision_bootstrap_proposals,
            "skip_reason": event.skip_reason,
        },
    })

    # ---- External notifications ----
    if event_name == "verdict" and event.verdict:
        issue_title = None
        if run and run.employee_report:
            try:
                report = json.loads(run.employee_report)
                issue_title = report.get("issue_title")
            except (json.JSONDecodeError, AttributeError):
                pass

        await send_notification(
            event_type=event.verdict,
            project=event.project or "unknown",
            issue_number=event.issue_number,
            issue_title=issue_title,
            tokens_total=run.tokens_total if run else None,
            summary=event.reasoning,
            run_id=event.run_id,
        )

    elif event_name == "finished" and event.status in ("error", "failed"):
        await send_notification(
            event_type="error",
            project=event.project or "unknown",
            tokens_total=event.tokens_total,
            run_id=event.run_id,
            summary=f"Run finished with status: {event.status}",
        )

    return {"status": "ok", "run_id": event.run_id, "event": event.event, "event_id": event.event_id}


async def _resolve_project_id(db: AsyncSession, project_name: str | None) -> int | None:
    """Try to match a project by full repo name or short name."""
    if not project_name:
        return None

    proj_result = await db.execute(
        select(Project).where(Project.repo == project_name)
    )
    proj = proj_result.scalar_one_or_none()
    if not proj:
        short = project_name.split("/")[-1] if "/" in project_name else project_name
        proj_result = await db.execute(select(Project))
        for p in proj_result.scalars().all():
            p_short = p.repo.split("/")[-1] if "/" in p.repo else p.repo
            if p_short == short:
                proj = p
                break
    return proj.id if proj else None


def _normalize_event_name(event_name: str) -> str:
    """Map run-manager.sh event names to internal handler names."""
    mapping = {
        "run_start": "started",
        "employee_start": "started",
        "employee_complete": "employee_done",
        "manager_review": "reviewing",
        "run_complete": "finished",
        "verdict_execute": "verdict",
        "plan_review_start": "plan_reviewing",
        "plan_review_complete": "plan_review_done",
        "started": "started",
        "finished": "finished",
        "verdict": "verdict",
        "task_started": "task_started",
        "task_completed": "task_completed",
        "task_failed": "task_failed",
        "task_ready": "task_ready",
        "task_blocked": "task_blocked",
        "conflict_detected": "conflict_detected",
        "guidance_sent": "guidance_sent",
        "dag_created": "dag_created",
        "dag_completed": "dag_completed",
        "queue_assigned": "queue_assigned",
        "queue_in_progress": "queue_in_progress",
        "queue_review": "queue_review",
        "queue_completed": "queue_completed",
        "queue_paused": "queue_paused",
        "queue_failed": "queue_failed",
        "orchestrator_start": "started",
        "orchestrator_complete": "finished",
        "orchestrator_error": "finished",
        "team_created": "team_created",
        "teammate_spawned": "teammate_spawned",
        "task_claimed": "task_claimed",
        "teammate_completed": "teammate_completed",
        "teammate_progress": "teammate_progress",
        "team_cleanup": "team_cleanup",
        "progress_update": "progress_update",
        "vision_misalignment": "vision_misalignment",
    }
    return mapping.get(event_name, event_name)
