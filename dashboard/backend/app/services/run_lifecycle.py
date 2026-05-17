"""Run lifecycle state management -- extracted from webhook.py.

Handles run creation, status transitions, token updates, employee reports,
and verdict recording. Each handler receives the DB session, event, project_id,
and existing run (if any), and returns the updated/created Run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoordinatorTask, Notification, Run, RunControl
from app.schemas import WebhookRunEvent
from app.services.event_bus import publish as event_bus_publish
from app.services.log_parser import parse_employee_report

logger = logging.getLogger(__name__)


# Sentinel value stored in ``RunControl.requested_by`` to mark rows that the
# lifecycle expirer swept after a run terminated with no orchestrator pickup.
# We avoid adding a new column so this is safe to deploy without a migration.
SWEEPER_EXPIRED = "sweeper-expired"


def _safe_repo_short(project: str | None) -> str | None:
    """Defense-in-depth validator for the ``project`` field before it reaches
    :func:`parse_employee_report`.

    The choke-point in ``log_parser`` already enforces filesystem containment
    (issue #189), but we reject obviously bogus values here too so that:

    1. Malformed inputs never even hit the filesystem.
    2. The intent is documented at every call site.

    Returns the trailing path component (after ``/``) on success, or ``None``
    if the value is empty, contains shell/path-traversal characters, or is a
    relative-path component that should not be used as a directory name.
    """
    if not project:
        return None
    # Match the existing extraction logic: take the trailing path component.
    repo_short = project.split("/")[-1] if "/" in project else project
    repo_short = repo_short.strip()
    if not repo_short:
        return None
    if repo_short in (".", ".."):
        return None
    if "/" in repo_short or "\\" in repo_short or "\x00" in repo_short:
        return None
    return repo_short


async def expire_orphan_controls(db: AsyncSession, run_id: str) -> list[dict]:
    """Mark every unconsumed run_control for ``run_id`` as expired and emit
    an SSE ``run_message_expired`` event per row.

    Called from :func:`handle_finished` when a run transitions to any terminal
    status so operators see their pending messages turn red instead of sitting
    silently in the queue forever. Returns the list of SSE payloads that were
    broadcast — useful for tests and debugging.

    Idempotent: rows that are already consumed (``consumed_at IS NOT NULL``)
    are ignored, so replaying a ``finished`` webhook won't double-fire.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(RunControl).where(
            RunControl.run_id == run_id,
            RunControl.consumed_at.is_(None),
        )
    )
    orphans = list(result.scalars().all())
    if not orphans:
        return []

    # Mark all in one UPDATE for atomicity.
    await db.execute(
        update(RunControl)
        .where(
            RunControl.run_id == run_id,
            RunControl.consumed_at.is_(None),
        )
        .values(consumed_at=now, requested_by=SWEEPER_EXPIRED)
    )

    published: list[dict] = []
    for row in orphans:
        payload: dict = {}
        if row.payload:
            try:
                payload = json.loads(row.payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        text_preview = str(payload.get("text") or "")[:500]
        data = {
            "run_id": run_id,
            "control_id": row.id,
            "action": row.action,
            "original_requested_by": row.requested_by,
            "text": text_preview,
            "expired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await event_bus_publish({"type": "run_message_expired", "data": data})
        published.append(data)

    logger.info(
        "Mission Control: expired %d orphan control(s) for terminated run %s",
        len(orphans), run_id,
    )
    return published


async def handle_started(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
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
            trace_id=event.trace_id,
            log_file=event.log_file,
        )
        db.add(run)
    else:
        run.status = "running"
        run.project_id = project_id or run.project_id
        run.mode = event.mode or run.mode
        run.model = event.model or run.model
        run.trace_id = event.trace_id or run.trace_id
        if event.log_file:
            run.log_file = event.log_file
        if event.employee_index is not None:
            run.employee_index = event.employee_index
        if event.concurrent_group_id:
            run.concurrent_group_id = event.concurrent_group_id
    return run


async def handle_finished(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    raw_status = event.status or "finished"
    status_map = {
        "success": "completed",
        "finished": "completed",
        "no_reports": "completed",
        "completed": "completed",
        "rate_limited": "completed",
        "skipped": "skipped",       # #446 #447: idle runs (no eligible work)
        "error": "failed",
        "interrupted": "interrupted",
    }
    final_status = status_map.get(raw_status, raw_status)

    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status=final_status,
            trace_id=event.trace_id,
        )
        db.add(run)

    run.status = final_status
    run.trace_id = event.trace_id or run.trace_id

    # Telemetry monotonicity (issue #454). The terminal ``finished`` event
    # is emitted by the orchestrator at run exit and historically clobbered
    # ``turns`` / ``tokens_*`` / ``cost_usd`` unconditionally. But these
    # same fields are also ratcheted upward by ``handle_progress_update``
    # (PR #438, issue #434) as the run proceeds — if the terminal payload
    # carries a lower value (e.g. an early teammate-scoped snapshot, or a
    # truncated final event) it would clobber a higher accumulated value.
    # Live repro: run-20260517T144539Z had turns=31 for 19 minutes then
    # was regressed to 7 by the finished event. Guard each cumulative
    # field with ``max(existing, incoming)`` and skip None entirely.
    if event.turns is not None:
        run.turns = max(run.turns or 0, event.turns)
    if event.tokens_input is not None:
        run.tokens_input = max(run.tokens_input or 0, event.tokens_input)
    if event.tokens_output is not None:
        run.tokens_output = max(run.tokens_output or 0, event.tokens_output)
    if event.tokens_total is not None:
        run.tokens_total = max(run.tokens_total or 0, event.tokens_total)
    if event.cost_usd is not None:
        run.cost_usd = max(run.cost_usd or 0.0, event.cost_usd)

    run.duration_ms = event.duration_ms
    run.finished_at = datetime.now(timezone.utc)

    # Cascade: any coordinator_tasks still claimed/running for this run are
    # marked 'orphaned' so /api/runs/active-employees does not resurrect
    # them as phantom employees after the parent run has finalised.
    # See issue #345 / spec 2026-05-11-run-lifecycle-overhaul-design.md.
    await db.execute(
        update(CoordinatorTask)
        .where(
            CoordinatorTask.run_id == event.run_id,
            CoordinatorTask.status.in_(("claimed", "running")),
        )
        .values(status="orphaned", claimed_at=None)
    )

    run.model = event.model or run.model
    if event.mode:
        run.mode = event.mode

    # Vision-bootstrap: only set when the event carries them so we don't
    # overwrite a regular run's NULLs with NULL-from-event.
    if event.vision_bootstrap_count is not None:
        run.vision_bootstrap_count = event.vision_bootstrap_count
    if event.vision_bootstrap_proposals is not None:
        run.vision_bootstrap_proposals = json.dumps(event.vision_bootstrap_proposals)
    if event.skip_reason is not None:
        run.skip_reason = event.skip_reason

    if not run.employee_report and event.project:
        repo_short = _safe_repo_short(event.project)
        if repo_short:
            report = parse_employee_report(repo_short)
            if report:
                run.employee_report = json.dumps(report)

    # Mission Control: the orchestrator for this run has exited, so any
    # pending run_control rows will never be drained. Mark them expired and
    # broadcast so the UI can flip them from "queued" (blue) to "expired"
    # (red) with a "Run ended before delivery" note.
    try:
        await expire_orphan_controls(db, run.run_id)
    except Exception as exc:  # pragma: no cover — best-effort sweep
        logger.warning(
            "Mission Control: failed to expire orphan controls for %s: %s",
            run.run_id, exc,
        )

    return run


async def handle_employee_done(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    if event.mode:
        run.mode = event.mode
    if event.model:
        run.model = event.model
    if project_id:
        run.project_id = project_id
    run.trace_id = event.trace_id or run.trace_id
    return run


async def handle_reviewing(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="reviewing",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "reviewing"
        run.trace_id = event.trace_id or run.trace_id
    return run


async def handle_verdict(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> tuple[Run, Notification]:
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            trace_id=event.trace_id,
        )
        db.add(run)

    run.verdict = event.verdict
    run.trace_id = event.trace_id or run.trace_id
    run.issue_number = event.issue_number
    run.branch = event.branch or run.branch
    run.verdict_detail = json.dumps({
        "verdict": event.verdict,
        "reasoning": event.reasoning or "",
        "project": event.project,
        "issue_number": event.issue_number,
        "branch": event.branch,
    })

    if not run.employee_report and event.project:
        repo_short = _safe_repo_short(event.project)
        if repo_short:
            report = parse_employee_report(repo_short)
            if report:
                run.employee_report = json.dumps(report)

    notification = Notification(
        run_id=event.run_id,
        type=event.verdict.lower() if event.verdict else "info",
        message=build_notification_message(event),
    )
    db.add(notification)

    return run, notification


async def handle_plan_reviewing(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="plan_reviewing",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "plan_reviewing"
        run.trace_id = event.trace_id or run.trace_id
    return run


async def handle_plan_review_done(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "running"
        run.trace_id = event.trace_id or run.trace_id
    return run


async def handle_awaiting_plan_review(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    """Plan-review gate (issue #266): plan_only run finished, manager verdict
    not yet applied. Set status to ``awaiting_plan_review`` so the dashboard
    banner can surface the gate.
    """
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="awaiting_plan_review",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "awaiting_plan_review"
        run.trace_id = event.trace_id or run.trace_id
    return run


async def handle_plan_approved(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    """Plan-review gate: APPROVE_PLAN — a follow-up ``full`` run has been
    enqueued. Terminal status for the plan_only run itself.
    """
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="plan_approved",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "plan_approved"
        run.trace_id = event.trace_id or run.trace_id
        if not run.finished_at:
            run.finished_at = datetime.now(timezone.utc)
    return run


async def handle_plan_rejected(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    """Plan-review gate: REJECT_PLAN or revisions exhausted — no follow-up
    run will be enqueued. Terminal status.
    """
    if not run:
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="plan_rejected",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    else:
        run.status = "plan_rejected"
        run.trace_id = event.trace_id or run.trace_id
        if not run.finished_at:
            run.finished_at = datetime.now(timezone.utc)
    return run


async def handle_progress_update(run: Run, event: WebhookRunEvent) -> None:
    """Update token/turn counts without changing run status.

    Telemetry is **monotonic**: we only ever ratchet these counters upward.
    Two distinct emitters write ``progress_update`` against the same ``runs``
    row — the orchestrator's outer cumulative counters and per-teammate
    ``teammate_progress`` events scoped to a single task_id. The teammate
    stream can legitimately carry 0 at the start of a fresh teammate cycle,
    which would otherwise clobber the cumulative value and produce the
    0↔N oscillation described in issue #434. Taking ``max(existing, incoming)``
    mirrors what ``coordinator_service`` already does for ``CoordinatorTask``
    and ensures the dashboard never regresses while a run is alive.
    """
    if event.tokens_input is not None:
        run.tokens_input = max(run.tokens_input or 0, event.tokens_input)
    if event.tokens_output is not None:
        run.tokens_output = max(run.tokens_output or 0, event.tokens_output)
    if event.tokens_total is not None:
        run.tokens_total = max(run.tokens_total or 0, event.tokens_total)
    if event.turns is not None:
        run.turns = max(run.turns or 0, event.turns)


async def handle_teammate_completed(run: Run, event: WebhookRunEvent) -> None:
    """Update team_members JSON when a teammate finishes."""
    if not event.task_id:
        return
    members = json.loads(run.team_members) if run.team_members else []
    for m in members:
        if m.get("task_id") == event.task_id:
            m["status"] = event.status or "completed"
            m["tokens_used"] = event.tokens_total or 0
            break
    run.team_members = json.dumps(members)


async def handle_team_member_spawn(run: Run, event: WebhookRunEvent) -> None:
    """Accumulate team members as JSON array on team creation/spawn events."""
    if event.team_name:
        run.team_name = event.team_name
    if event.agent_name and event.agent_id:
        members = json.loads(run.team_members) if run.team_members else []
        if not any(m.get("agent_id") == event.agent_id for m in members):
            members.append({
                "agent_id": event.agent_id or "",
                "name": event.agent_name or "",
                "status": "spawned",
            })
            run.team_members = json.dumps(members)


async def handle_unknown(
    db: AsyncSession, event: WebhookRunEvent, project_id: int | None, run: Run | None
) -> Run:
    """Handle unknown event types -- still create/update run record.

    Run-level state contract (#453): this fallback handler does NOT mirror
    ``event.status`` onto ``run.status``. The mirror was a latent foot-gun
    behind issue #450 — ``teammate_completed`` carried the teammate's
    terminal status, fell through to ``handle_unknown``, and flipped the
    parent run to ``completed`` even though the run was still alive.
    PR #452 closed that specific offender via an explicit dispatcher
    entry; #453 removes the underlying foot-gun so any FUTURE unmapped
    event that should affect ``run.status`` must land in
    ``_RUN_HANDLERS`` explicitly rather than silently mutating state.
    """
    if not run:
        # NOTE: a brand-new run materialised from an unmapped event has no
        # established status to preserve, so default to ``running`` rather
        # than adopting the event's status (which may be teammate-scoped).
        run = Run(
            run_id=event.run_id,
            project_id=project_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            trace_id=event.trace_id,
        )
        db.add(run)
    if event.mode:
        run.mode = event.mode
    if event.model:
        run.model = event.model
    if project_id:
        run.project_id = project_id
    run.trace_id = event.trace_id or run.trace_id
    return run


def build_notification_message(event: WebhookRunEvent) -> str:
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
