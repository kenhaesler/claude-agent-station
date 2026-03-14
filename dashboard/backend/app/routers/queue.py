"""Task queue CRUD with state machine validation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import QueueItem
from app.schemas import (
    BackpressureStatus,
    QueueItemCreate,
    QueueItemList,
    QueueItemOut,
    QueueItemUpdate,
    QueueStats,
)
from app.services.event_bus import publish as event_bus_publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"])

# Valid state transitions (expanded for orchestration overhaul)
TRANSITIONS: dict[str, set[str]] = {
    "pending":     {"assigned", "claimed", "planning", "paused", "failed", "cancelled"},
    "claimed":     {"in_progress", "pending", "paused"},
    "assigned":    {"in_progress", "pending", "paused"},
    "planning":    {"in_progress", "paused", "failed", "pending"},
    "in_progress": {"review", "verifying", "paused", "failed", "pending"},
    "verifying":   {"approved", "rejected", "pending"},
    "review":      {"approved", "rejected", "pending"},
    "approved":    {"completed"},
    "rejected":    {"pending", "failed", "escalated"},
    "escalated":   {"pending"},
    "paused":      {"pending"},
    "failed":      {"pending"},
    "cancelled":   set(),
}

ALL_STATES = set(TRANSITIONS.keys()) | {"completed"}

# Active states for deduplication checks
ACTIVE_STATES = {"pending", "claimed", "assigned", "planning", "in_progress", "review", "verifying"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("", response_model=QueueItemList)
async def list_queue(
    state: str | None = Query(None),
    project_repo: str | None = Query(None),
    run_id: str | None = Query(None),
    mode: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = select(QueueItem)
    count_q = select(func.count(QueueItem.id))

    if state:
        q = q.where(QueueItem.state == state)
        count_q = count_q.where(QueueItem.state == state)
    if project_repo:
        q = q.where(QueueItem.project_repo == project_repo)
        count_q = count_q.where(QueueItem.project_repo == project_repo)
    if run_id:
        q = q.where(QueueItem.run_id == run_id)
        count_q = count_q.where(QueueItem.run_id == run_id)
    if mode:
        q = q.where(QueueItem.mode == mode)
        count_q = count_q.where(QueueItem.mode == mode)

    q = q.order_by(QueueItem.priority.desc(), QueueItem.created_at.desc())
    q = q.offset(offset).limit(limit)

    result = await db.execute(q)
    items = result.scalars().all()
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    return QueueItemList(items=[QueueItemOut.model_validate(i) for i in items], total=total)


@router.get("/stats", response_model=QueueStats)
async def queue_stats(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(QueueItem.state, func.count(QueueItem.id)).group_by(QueueItem.state)
    )
    by_state = {row[0]: row[1] for row in result.all()}
    total = sum(by_state.values())

    # Average time to complete (completed items with both timestamps)
    avg_result = await db.execute(
        select(
            func.avg(
                func.julianday(QueueItem.completed_at) - func.julianday(QueueItem.created_at)
            )
        ).where(
            QueueItem.state == "completed",
            QueueItem.completed_at.isnot(None),
        )
    )
    avg_days = avg_result.scalar()
    avg_ms = avg_days * 86_400_000 if avg_days else None

    return QueueStats(by_state=by_state, total=total, avg_time_to_complete_ms=avg_ms)


@router.get("/pressure", response_model=BackpressureStatus)
async def queue_pressure(db: AsyncSession = Depends(get_db)):
    """Get current backpressure status.

    Calculates load based on active queue items vs capacity.
    """
    from app.services.backpressure import calculate_backpressure

    # Count active items
    result = await db.execute(
        select(func.count(QueueItem.id)).where(
            QueueItem.state.in_(["claimed", "assigned", "in_progress", "planning", "verifying"])
        )
    )
    active_count = result.scalar() or 0

    # Simple heuristic: treat active items / 5 as utilization percentage
    # In production, this would use actual plan/token usage data
    usage = min(100, active_count * 20)

    state = calculate_backpressure(usage, base_max_concurrent=3)
    return BackpressureStatus(
        level=state.level,
        usage_percent=state.usage_percent,
        max_concurrent=state.max_concurrent,
        effective_concurrent=state.effective_concurrent,
        model_restriction=state.model_restriction,
        turn_cap=state.turn_cap,
    )


@router.get("/{item_id}", response_model=QueueItemOut)
async def get_queue_item(item_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(QueueItem).where(QueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")
    return QueueItemOut.model_validate(item)


@router.post("", response_model=QueueItemOut, status_code=201)
async def create_queue_item(
    data: QueueItemCreate,
    db: AsyncSession = Depends(get_db),
):
    # Deduplication: check for active item on same issue
    if data.issue_number is not None:
        existing = await db.execute(
            select(QueueItem).where(
                QueueItem.project_repo == data.project_repo,
                QueueItem.issue_number == data.issue_number,
                QueueItem.state.in_(ACTIVE_STATES),
            )
        )
        dup = existing.scalar_one_or_none()
        if dup:
            logger.info(
                "Deduplicated queue item: %s #%d already has active item %d",
                data.project_repo, data.issue_number, dup.id,
            )
            return QueueItemOut.model_validate(dup)

    item = QueueItem(
        project_repo=data.project_repo,
        issue_number=data.issue_number,
        issue_title=data.issue_title,
        state=data.state,
        priority=data.priority,
        assigned_to=data.assigned_to,
        run_id=data.run_id,
        max_retries=data.max_retries,
        context=data.context,
        mode=data.mode,
        complexity_score=data.complexity_score,
        escalation_rung=data.escalation_rung,
        escalated_from=data.escalated_from,
        parent_task_id=data.parent_task_id,
        handoff_context=data.handoff_context,
    )
    if data.state == "assigned":
        item.assigned_at = _utcnow()
    db.add(item)
    await db.commit()
    await db.refresh(item)

    await event_bus_publish({
        "type": f"queue_{item.state}",
        "data": {"queue_item_id": item.id, "project_repo": item.project_repo, "state": item.state},
    })

    logger.info("Queue item %d created: %s state=%s mode=%s", item.id, item.project_repo, item.state, item.mode)
    return QueueItemOut.model_validate(item)


@router.post("/claim", response_model=QueueItemOut | None)
async def claim_work(
    employee_index: int = Query(...),
    run_id: str = Query(...),
    project_repo: str | None = Query(None),
    mode: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Atomically claim the highest-priority pending item (work-stealing pattern).

    Returns the claimed item, or 204 No Content if nothing is available.
    """
    q = (
        select(QueueItem)
        .where(QueueItem.state == "pending")
        .order_by(QueueItem.priority.desc(), QueueItem.created_at.asc())
        .limit(1)
    )
    if project_repo:
        q = q.where(QueueItem.project_repo == project_repo)
    if mode:
        q = q.where(QueueItem.mode == mode)

    result = await db.execute(q)
    item = result.scalar_one_or_none()
    if not item:
        return Response(status_code=204)

    now = _utcnow()
    item.state = "claimed"
    item.assigned_to = employee_index
    item.run_id = run_id
    item.assigned_at = now
    item.updated_at = now
    await db.commit()
    await db.refresh(item)

    await event_bus_publish({
        "type": "queue_claimed",
        "data": {"queue_item_id": item.id, "project_repo": item.project_repo, "employee_index": employee_index},
    })

    logger.info("Queue item %d claimed by employee %d (run %s)", item.id, employee_index, run_id)
    return QueueItemOut.model_validate(item)


@router.put("/{item_id}", response_model=QueueItemOut)
async def update_queue_item(
    item_id: int,
    data: QueueItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QueueItem).where(QueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")

    # Validate state transition
    if data.state and data.state != item.state:
        allowed = TRANSITIONS.get(item.state, set())
        if data.state not in allowed:
            raise HTTPException(
                400,
                f"Invalid state transition: {item.state} → {data.state}. "
                f"Allowed: {sorted(allowed)}",
            )

        item.state = data.state

        # Set lifecycle timestamps
        now = _utcnow()
        if data.state in ("assigned", "claimed"):
            item.assigned_at = now
        elif data.state == "in_progress":
            item.started_at = now
        elif data.state == "completed":
            item.completed_at = now

    # Apply other fields (use model_fields_set to detect explicitly provided values,
    # including explicit nulls like {"run_id": null} for clearing fields)
    for field in ("priority", "assigned_to", "run_id", "employee_report",
                  "manager_feedback", "retry_count", "error_message", "context",
                  "mode", "complexity_score", "escalation_rung", "confidence",
                  "handoff_context"):
        if field in data.model_fields_set:
            setattr(item, field, getattr(data, field))

    item.updated_at = _utcnow()
    await db.commit()
    await db.refresh(item)

    # Publish SSE event on state change
    if data.state:
        await event_bus_publish({
            "type": f"queue_{data.state}",
            "data": {"queue_item_id": item.id, "project_repo": item.project_repo, "state": item.state},
        })

    logger.info("Queue item %d updated: state=%s", item.id, item.state)
    return QueueItemOut.model_validate(item)


@router.delete("/{item_id}", status_code=204)
async def delete_queue_item(item_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(QueueItem).where(QueueItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.state not in ("pending", "paused", "failed", "completed", "cancelled"):
        raise HTTPException(400, f"Can only delete terminal/idle items (current: {item.state})")
    await db.delete(item)
    await db.commit()


class BatchPauseRequest(BaseModel):
    run_id: str


@router.post("/batch-pause")
async def batch_pause(
    body: BatchPauseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueueItem).where(
            QueueItem.run_id == body.run_id,
            QueueItem.state.in_(["assigned", "claimed", "in_progress"]),
        )
    )
    items = result.scalars().all()
    count = 0
    for item in items:
        item.state = "paused"
        item.updated_at = _utcnow()
        count += 1
    await db.commit()

    if count > 0:
        await event_bus_publish({
            "type": "queue_paused",
            "data": {"run_id": body.run_id, "paused_count": count},
        })

    logger.info("Batch paused %d items for run %s", count, body.run_id)
    return {"status": "ok", "paused": count}


@router.post("/purge", status_code=200)
async def purge_completed(
    max_age_days: int = Query(default=7, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Delete completed and failed queue items older than max_age_days."""
    cutoff = _utcnow() - timedelta(days=max_age_days)
    result = await db.execute(
        select(QueueItem).where(
            QueueItem.state.in_(["completed", "failed", "cancelled"]),
            QueueItem.updated_at < cutoff,
        )
    )
    items = result.scalars().all()
    for item in items:
        await db.delete(item)
    await db.commit()

    if items:
        logger.info("Purged %d completed/failed queue items older than %d days", len(items), max_age_days)

    return {"purged": len(items)}
