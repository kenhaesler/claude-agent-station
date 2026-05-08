"""Audit log API — per-tool-call telemetry timeline (issue #73).

Boundary vs. ``/api/agent-events``: ``agent_events`` records orchestration
decisions (auto-mode allow/deny, workflow state). ``audit_log`` records the
*actions* an employee actually executed and their outcome (status, exit code,
stdout/stderr tails, durations). One row per tool call, written in two phases
(PreToolUse → PostToolUse) and matched by ``idempotency_key`` (= SDK
``tool_use_id``) so retries collapse into a single row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import AuditEntry
from app.schemas import AuditEntryOut, AuditStats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit_entries(
    run_id: str | None = Query(None, description="Filter by run id"),
    trace_id: str | None = Query(None, description="Filter by workflow trace id"),
    action_kind: str | None = Query(None, description="Filter by action kind, e.g. tool.bash"),
    status: str | None = Query(None, description="Filter by status: started/ok/error/timeout"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return audit entries ordered by ``started_at``.

    At least one of ``run_id`` or ``trace_id`` must be supplied to bound the
    query — unfiltered scans of an unbounded append-only log are not exposed.
    """
    if not run_id and not trace_id:
        raise HTTPException(
            status_code=400,
            detail="At least one of run_id or trace_id is required",
        )

    q = select(AuditEntry)
    if run_id:
        q = q.where(AuditEntry.run_id == run_id)
    if trace_id:
        q = q.where(AuditEntry.trace_id == trace_id)
    if action_kind:
        q = q.where(AuditEntry.action_kind == action_kind)
    if status:
        q = q.where(AuditEntry.status == status)
    q = q.order_by(AuditEntry.started_at.asc(), AuditEntry.id.asc()).offset(offset).limit(limit)

    result = await db.execute(q)
    rows = result.scalars().all()
    return [AuditEntryOut.model_validate(r) for r in rows]


@router.get("/stats", response_model=AuditStats)
async def audit_stats(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Action-kind distribution + error rate + avg duration over the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    by_kind_q = (
        select(AuditEntry.action_kind, func.count(AuditEntry.id))
        .where(AuditEntry.started_at >= since)
        .group_by(AuditEntry.action_kind)
    )
    by_kind = {row[0]: row[1] for row in (await db.execute(by_kind_q)).all()}
    total = sum(by_kind.values())

    # Error rate: rows whose terminal status is "error" or "timeout".
    # "started" rows (still in flight) are excluded from the denominator.
    err_q = select(
        func.count(AuditEntry.id),
        func.sum(case((AuditEntry.status.in_(("error", "timeout")), 1), else_=0)),
    ).where(
        AuditEntry.started_at >= since,
        AuditEntry.status != "started",
    )
    counted, errored = (await db.execute(err_q)).one()
    counted = counted or 0
    errored = errored or 0
    error_rate = (errored / counted) if counted else 0.0

    # Average duration in ms for finished rows only.
    dur_q = select(
        func.avg(
            (func.julianday(AuditEntry.finished_at) - func.julianday(AuditEntry.started_at))
            * 86_400_000.0
        )
    ).where(
        AuditEntry.started_at >= since,
        AuditEntry.finished_at.is_not(None),
    )
    avg_ms = (await db.execute(dur_q)).scalar()

    return AuditStats(
        days=days,
        total=total,
        by_kind=by_kind,
        error_rate=round(error_rate, 4),
        avg_duration_ms=round(float(avg_ms), 2) if avg_ms is not None else None,
    )
