"""Coordinator API: task DAG status, messages, and guidance."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import CoordinatorTask, CoordinatorMessage
from app.schemas import CoordinatorTaskOut, CoordinatorDAGOut, CoordinatorMessageOut, GuidanceSend
from app.services.event_bus import publish as event_bus_publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coordinator", tags=["coordinator"])


@router.get("/tasks", response_model=list[CoordinatorTaskOut])
async def list_tasks(
    run_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List coordinator tasks, optionally filtered by run_id and status."""
    query = select(CoordinatorTask).order_by(CoordinatorTask.created_at)
    if run_id:
        query = query.where(CoordinatorTask.run_id == run_id)
    if status:
        query = query.where(CoordinatorTask.status == status)
    query = query.limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/tasks/{task_id}", response_model=CoordinatorTaskOut)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single coordinator task."""
    result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/dag/{run_id}", response_model=CoordinatorDAGOut)
async def get_dag(run_id: str, db: AsyncSession = Depends(get_db)):
    """Get the full task DAG for a run."""
    result = await db.execute(
        select(CoordinatorTask)
        .where(CoordinatorTask.run_id == run_id)
        .order_by(CoordinatorTask.created_at)
    )
    tasks = result.scalars().all()
    if not tasks:
        raise HTTPException(status_code=404, detail="No tasks found for this run")

    # Build summary
    summary: dict[str, int] = {}
    for t in tasks:
        summary[t.status] = summary.get(t.status, 0) + 1

    return CoordinatorDAGOut(
        run_id=run_id,
        project_repo=tasks[0].project_repo,
        tasks=tasks,
        summary=summary,
    )


@router.get("/messages", response_model=list[CoordinatorMessageOut])
async def list_messages(
    run_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List coordinator messages, optionally filtered."""
    query = select(CoordinatorMessage).order_by(desc(CoordinatorMessage.created_at))
    if run_id:
        query = query.where(CoordinatorMessage.run_id == run_id)
    if task_id:
        query = query.where(CoordinatorMessage.task_id == task_id)
    query = query.limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/guidance")
async def send_guidance_api(
    payload: GuidanceSend,
    db: AsyncSession = Depends(get_db),
):
    """Send guidance to an employee from the dashboard UI."""
    from agent.coordinator.guidance import send_guidance

    # Determine workspace: either from payload or from the task's workspace
    workspace = payload.workspace
    if not workspace:
        # Try to find workspace from running tasks
        result = await db.execute(
            select(CoordinatorTask).where(
                CoordinatorTask.run_id == payload.run_id,
                CoordinatorTask.employee_index == payload.employee_index,
                CoordinatorTask.status == "running",
            )
        )
        task = result.scalar_one_or_none()
        if task and task.workspace:
            workspace = task.workspace
        else:
            raise HTTPException(
                status_code=400,
                detail="Cannot determine workspace. Provide workspace or ensure employee is running.",
            )

    try:
        send_guidance(workspace, payload.employee_index, payload.guidance_type, payload.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send guidance: {e}")

    # Record the message
    msg = CoordinatorMessage(
        run_id=payload.run_id,
        direction="to_employee",
        message_type="guidance",
        content=json.dumps({
            "type": payload.guidance_type,
            "content": payload.content,
        }),
        employee_index=payload.employee_index,
    )
    db.add(msg)
    await db.commit()

    # Broadcast via SSE
    await event_bus_publish({
        "type": "guidance_sent",
        "data": {
            "run_id": payload.run_id,
            "employee_index": payload.employee_index,
            "guidance_type": payload.guidance_type,
        },
    })

    return {"status": "ok", "message": "Guidance sent"}
