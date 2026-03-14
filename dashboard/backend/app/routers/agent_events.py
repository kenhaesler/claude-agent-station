"""Agent event log API — append-only structured audit trail."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import AgentEvent
from app.schemas import AgentEventCreate, AgentEventOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-events", tags=["agent-events"])


@router.post("", status_code=201, response_model=AgentEventOut)
async def record_event(
    data: AgentEventCreate,
    db: AsyncSession = Depends(get_db),
):
    """Append a new event to the agent event log."""
    event = AgentEvent(
        workflow_id=data.workflow_id,
        run_id=data.run_id,
        agent_id=data.agent_id,
        event_type=data.event_type,
        event_data=data.event_data,
        parent_event_id=data.parent_event_id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    logger.debug("Event recorded: %s/%s type=%s", data.workflow_id, data.agent_id, data.event_type)
    return AgentEventOut.model_validate(event)


@router.get("/{workflow_id}", response_model=list[AgentEventOut])
async def get_workflow_events(
    workflow_id: str,
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get all events for a workflow, optionally filtered by type."""
    q = select(AgentEvent).where(AgentEvent.workflow_id == workflow_id)
    if event_type:
        q = q.where(AgentEvent.event_type == event_type)
    q = q.order_by(AgentEvent.created_at.asc()).limit(limit)

    result = await db.execute(q)
    events = result.scalars().all()
    return [AgentEventOut.model_validate(e) for e in events]


@router.get("", response_model=list[AgentEventOut])
async def list_recent_events(
    event_type: str | None = Query(None),
    agent_id: str | None = Query(None),
    run_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List recent events across all workflows."""
    q = select(AgentEvent)
    if event_type:
        q = q.where(AgentEvent.event_type == event_type)
    if agent_id:
        q = q.where(AgentEvent.agent_id == agent_id)
    if run_id:
        q = q.where(AgentEvent.run_id == run_id)
    q = q.order_by(AgentEvent.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(q)
    events = result.scalars().all()
    return [AgentEventOut.model_validate(e) for e in events]


@router.get("/stats/summary")
async def event_stats(db: AsyncSession = Depends(get_db)):
    """Get summary statistics of events by type."""
    result = await db.execute(
        select(AgentEvent.event_type, func.count(AgentEvent.event_id))
        .group_by(AgentEvent.event_type)
    )
    by_type = {row[0]: row[1] for row in result.all()}
    total_result = await db.execute(select(func.count(AgentEvent.event_id)))
    total = total_result.scalar() or 0
    return {"by_type": by_type, "total": total}
