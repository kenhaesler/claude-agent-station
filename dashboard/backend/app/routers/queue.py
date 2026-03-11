"""Task queue CRUD with state machine validation."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import QueueItem
from app.schemas import (
    QueueItemCreate,
    QueueItemUpdate,
    QueueItemOut,
    QueueItemList,
    QueueStats,
)
from app.services.event_bus import publish as event_bus_publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"])

# Valid state transitions
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"assigned", "paused", "failed"},
    "assigned": {"in_progress", "pending", "paused"},
    "in_progress": {"review", "paused", "failed"},
    "review": {"approved", "rejected"},
    "approved": {"completed"},
    "rejected": {"pending", "failed"},
    "paused": {"pending"},
    "failed": {"pending"},
}

ALL_STATES = set(TRANSITIONS.keys()) | {"completed"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("", response_model=QueueItemList)
async def list_queue(
    state: Optional[str] = Query(None),
    project_repo: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
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

    logger.info("Queue item %d created: %s state=%s", item.id, item.project_repo, item.state)
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
        if data.state == "assigned":
            item.assigned_at = now
        elif data.state == "in_progress":
            item.started_at = now
        elif data.state == "completed":
            item.completed_at = now

    # Apply other fields
    for field in ("priority", "assigned_to", "run_id", "employee_report",
                  "manager_feedback", "retry_count", "error_message", "context"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(item, field, val)

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
    if item.state not in ("pending", "paused", "failed"):
        raise HTTPException(400, f"Can only delete pending/paused/failed items (current: {item.state})")
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
            QueueItem.state.in_(["assigned", "in_progress"]),
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
