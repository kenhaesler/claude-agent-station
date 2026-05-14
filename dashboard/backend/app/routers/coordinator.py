"""Coordinator API: task DAG status, messages, and guidance."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import CoordinatorMessage, CoordinatorTask, Run
from app.services.json_compat import decode_event_data
from app.schemas import (
    CoordinatorDAGOut,
    CoordinatorMessageOut,
    CoordinatorTaskCreate,
    CoordinatorTaskDetailOut,
    CoordinatorTaskOut,
    CoordinatorTaskUpdate,
    GuidanceSend,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coordinator", tags=["coordinator"])


@router.get("/tasks", response_model=list[CoordinatorTaskOut])
async def list_tasks(
    run_id: str | None = Query(None),
    status: str | None = Query(None),
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


@router.post("/tasks", response_model=CoordinatorTaskOut, status_code=201)
async def create_task(
    payload: CoordinatorTaskCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a coordinator task (called by agent.coordinator_lifecycle).

    Generates a stable id from run_id and the current task count for the run so
    that retried creates are easy to track. The status defaults to 'running'
    so the active-employees endpoint surfaces the task immediately.
    """
    import uuid
    from datetime import datetime, timezone

    # Generate a unique task id: "task-{run_id}-{short_uuid}"
    task_id = f"task-{payload.run_id}-{uuid.uuid4().hex[:8]}"
    task = CoordinatorTask(
        id=task_id,
        run_id=payload.run_id,
        project_repo=payload.project_repo,
        issue_number=payload.issue_number,
        employee_index=payload.employee_index,
        status=payload.status,
        title=payload.title or "",
        description=payload.description,
        started_at=datetime.now(timezone.utc) if payload.status == "running" else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.put("/tasks/{task_id}", response_model=CoordinatorTaskOut)
async def update_task(
    task_id: str,
    payload: CoordinatorTaskUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a coordinator task lifecycle status (called by agent.coordinator_lifecycle).

    Accepted statuses: completed, failed, orphaned, running, blocked, ready.
    Sets finished_at when a terminal status (completed/failed/orphaned) is written.
    """
    from datetime import datetime, timezone

    result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = payload.status
    if payload.result_summary is not None:
        task.result_summary = payload.result_summary
    if payload.error_message is not None:
        task.error_message = payload.error_message
    if payload.exit_code is not None:
        task.exit_code = payload.exit_code
    if payload.branch is not None:
        task.branch = payload.branch
    if payload.touched_files is not None:
        task.touched_files = payload.touched_files

    terminal_statuses = {"completed", "failed", "orphaned"}
    if payload.status in terminal_statuses and task.finished_at is None:
        task.finished_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(task)
    return task


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


@router.get("/tasks/{task_id}/details", response_model=CoordinatorTaskDetailOut)
async def get_task_details(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get extended task details including employee report and log excerpt."""
    import os

    result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Build base response from task attributes
    task_data = CoordinatorTaskDetailOut.model_validate(task)

    # Try to find matching run's employee report
    if task.run_id and task.employee_index is not None:
        run_result = await db.execute(
            select(Run).where(
                Run.run_id == task.run_id,
                Run.employee_index == task.employee_index,
            )
        )
        run = run_result.scalar_one_or_none()
        if run and run.employee_report:
            task_data.employee_report = decode_event_data(run.employee_report)

        # Use run's log_file if task doesn't have log_path
        if not task.log_path and run and run.log_file:
            task_data.log_path = run.log_file

    # Read a log excerpt (last 100 lines)
    log_file = task.log_path or (task_data.log_path if task_data.log_path else None)
    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, errors="replace") as f:
                lines = f.readlines()
                task_data.log_excerpt = "".join(lines[-100:])
        except OSError:
            task_data.log_excerpt = None

    return task_data


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
    run_id: str | None = Query(None),
    task_id: str | None = Query(None),
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
    """Legacy shim — forwards to the Mission Control run_controls queue.

    The dashboard's GuidanceInput component should call
    ``POST /api/runs/{run_id}/message`` directly going forward, but this
    shim keeps any older caller working by translating ``guidance_type`` +
    ``content`` into a control-queue message.
    """
    import json as _json

    from app.models import Run, RunControl
    from app.services.event_bus import publish

    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")

    # Verify the run exists and is still running — guidance on a completed
    # run is a no-op and we want to surface that clearly. Treat any terminal
    # status (completed/failed/interrupted) or a non-null finished_at as
    # "orchestrator has exited, queue will never drain" → 409.
    result = await db.execute(select(Run).where(Run.run_id == payload.run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if (run.status or "") in {"completed", "failed", "interrupted"} or run.finished_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {payload.run_id} is no longer active "
                f"(status={run.status or 'unknown'}) — guidance has no effect."
            ),
        )

    # Map legacy guidance_type → orchestrator control action. 'stop' maps
    # to the hard stop path; everything else becomes an injected message
    # so the lead agent can read it in-context.
    if (payload.guidance_type or "").lower() == "stop":
        action = "stop"
        control_payload = None
    else:
        action = "message"
        prefix = f"[operator-{payload.guidance_type or 'info'}] "
        control_payload = {"text": prefix + payload.content.strip()}

    row = RunControl(
        run_id=payload.run_id,
        action=action,
        payload=_json.dumps(control_payload) if control_payload else None,
        requested_by="guidance",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await publish({
        "type": f"run_control_{action}",
        "data": {
            "run_id": payload.run_id,
            "control_id": row.id,
            "requested_by": "guidance",
            "payload": control_payload or {},
        },
    })

    return {
        "status": "ok",
        "action": action,
        "control_id": row.id,
    }
