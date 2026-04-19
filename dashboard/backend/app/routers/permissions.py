"""Permission tray endpoints (ADR-0001, P2.T10).

The policy engine writes rows into `permission_requests`; the dashboard
lists pending rows, shows an approve/deny UI, and resolves the request.
A 5-minute timer auto-denies rows the operator ignores.

Row lifecycle:
    pending --(approve)--> approved
    pending --(deny)----> denied
    pending --(timeout)-> timed_out
    <terminal> is immutable

The agent process polls its own row via `GET /api/runs/{run_id}/permissions/{request_id}`
and only proceeds once the row leaves 'pending'. That polling path lives
in the agent; this router owns the operator side.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import PermissionRequest
from app.schemas import PermissionCreateIn, PermissionDecisionIn, PermissionRequestOut
from app.services import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _timeout_seconds() -> int:
    raw = os.environ.get("STATION_PERMISSION_TRAY_TIMEOUT_SECONDS", "300")
    try:
        return max(30, int(raw))
    except ValueError:
        return 300


async def _timeout_expired_rows(db: AsyncSession) -> list[PermissionRequest]:
    """Flip any pending rows older than the timeout to timed_out."""
    horizon = datetime.now(timezone.utc) - timedelta(seconds=_timeout_seconds())
    stmt = select(PermissionRequest).where(
        PermissionRequest.status == "pending",
        PermissionRequest.created_at <= horizon,
    )
    result = await db.execute(stmt)
    expired = list(result.scalars().all())
    if not expired:
        return []
    now = datetime.now(timezone.utc)
    for row in expired:
        row.status = "timed_out"
        row.resolved_at = now
        row.resolution_note = (
            f"auto-denied after {_timeout_seconds()}s without operator response"
        )
    await db.commit()
    for row in expired:
        await event_bus.publish({
            "type": "permission_resolved",
            "data": {
                "request_id": row.request_id,
                "status": row.status,
                "run_id": row.run_id,
                "note": row.resolution_note,
            },
        })
    return expired


@router.post("", response_model=PermissionRequestOut, status_code=201)
async def create_permission_request_endpoint(
    payload: PermissionCreateIn,
    db: AsyncSession = Depends(get_db),
):
    """Agent-facing: raise a new tray request. Idempotent on request_id — a
    second POST with an existing id returns the current row so the agent can
    safely retry after a transient network blip."""
    existing = await db.execute(
        select(PermissionRequest).where(PermissionRequest.request_id == payload.request_id)
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row

    return await create_permission_request(
        db,
        request_id=payload.request_id,
        run_id=payload.run_id,
        agent_id=payload.agent_id,
        tool_name=payload.tool_name,
        tool_input=payload.tool_input,
        autonomy_level=payload.autonomy_level,
        reason=payload.reason,
    )


@router.get("", response_model=list[PermissionRequestOut])
async def list_permission_requests(
    status: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List permission requests. Default status=pending, oldest first."""
    await _timeout_expired_rows(db)

    stmt = select(PermissionRequest)
    effective_status = status or "pending"
    if effective_status != "all":
        stmt = stmt.where(PermissionRequest.status == effective_status)
    if run_id:
        stmt = stmt.where(PermissionRequest.run_id == run_id)
    stmt = stmt.order_by(PermissionRequest.created_at.asc()).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{request_id}", response_model=PermissionRequestOut)
async def get_permission_request(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _timeout_expired_rows(db)
    result = await db.execute(
        select(PermissionRequest).where(PermissionRequest.request_id == request_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="permission request not found")
    return row


@router.post("/{request_id}", response_model=PermissionRequestOut)
async def resolve_permission_request(
    request_id: str,
    payload: PermissionDecisionIn,
    db: AsyncSession = Depends(get_db),
):
    """Operator resolves a pending request. Idempotent in terminal states:
    calls against already-resolved rows return 409 rather than silently
    overwriting — we don't want to flip an expired auto-deny to approve."""
    # Run a timeout sweep first so the operator can't act on a row that has
    # already expired on the server side.
    await _timeout_expired_rows(db)

    result = await db.execute(
        select(PermissionRequest).where(PermissionRequest.request_id == request_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="permission request not found")

    if row.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"request already resolved (status={row.status})",
        )

    row.status = "approved" if payload.decision == "approve" else "denied"
    row.resolution_note = payload.note
    row.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)

    await event_bus.publish({
        "type": "permission_resolved",
        "data": {
            "request_id": row.request_id,
            "status": row.status,
            "run_id": row.run_id,
            "note": row.resolution_note,
        },
    })
    logger.info(
        "permission %s resolved: %s (run=%s, tool=%s)",
        row.request_id, row.status, row.run_id, row.tool_name,
    )
    return row


async def create_permission_request(
    db: AsyncSession,
    *,
    request_id: str,
    run_id: str,
    agent_id: str,
    tool_name: str,
    tool_input: dict,
    autonomy_level: str,
    reason: str | None = None,
) -> PermissionRequest:
    """Helper used by the policy engine to raise a new request.

    Also publishes a `permission_request` SSE event so the dashboard can
    pop the tray without polling.
    """
    row = PermissionRequest(
        request_id=request_id,
        run_id=run_id,
        agent_id=agent_id,
        tool_name=tool_name,
        tool_input=json.dumps(tool_input, default=str),
        autonomy_level=autonomy_level,
        reason=reason,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await event_bus.publish({
        "type": "permission_request",
        "data": {
            "request_id": row.request_id,
            "run_id": row.run_id,
            "agent_id": row.agent_id,
            "tool_name": row.tool_name,
            "tool_input": tool_input,
            "autonomy_level": row.autonomy_level,
            "reason": row.reason,
        },
    })
    return row
