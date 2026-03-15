"""Run history endpoints."""

from __future__ import annotations

import asyncio
from typing import Optional
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import AgentEvent, CoordinatorMessage, CoordinatorTask, Plan, Project, QueueItem, Run
from app.schemas import (
    ActiveEmployeeOut,
    AgentEventOut,
    CoordinatorMessageOut,
    CoordinatorTaskOut,
    PlanOut,
    QueueItemOut,
    RunFullContext,
    RunList,
    RunOut,
)
from app.services.diff_parser import DiffResult, parse_unified_diff
from app.services.log_importer import import_historical_runs
from app.services.systemd import systemctl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=RunList)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_id: int | None = None,
    status: str | None = None,
    verdict: str | None = None,
    concurrent_group_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Run)
    count_query = select(func.count(Run.id))

    if project_id is not None:
        query = query.where(Run.project_id == project_id)
        count_query = count_query.where(Run.project_id == project_id)
    if status:
        query = query.where(Run.status == status)
        count_query = count_query.where(Run.status == status)
    if verdict:
        query = query.where(Run.verdict == verdict)
        count_query = count_query.where(Run.verdict == verdict)
    if concurrent_group_id:
        query = query.where(Run.concurrent_group_id == concurrent_group_id)
        count_query = count_query.where(Run.concurrent_group_id == concurrent_group_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(desc(Run.started_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    runs = result.scalars().all()

    return RunList(runs=runs, total=total)


@router.get("/active-employees", response_model=list[ActiveEmployeeOut])
async def get_active_employees(db: AsyncSession = Depends(get_db)):
    """Return all currently running employee/agent runs for workspace visualization.

    Queries Run records first. If only 1 Run is found but there are multiple
    running CoordinatorTasks, synthesize additional ActiveEmployeeOut entries
    from the coordinator tasks (handles the coordinated multi-employee path).
    """
    result = await db.execute(
        select(Run).where(Run.status.in_(["running", "plan_reviewing", "reviewing"]))
    )
    runs = result.scalars().all()
    employees = [
        ActiveEmployeeOut(
            run_id=r.run_id,
            project_id=r.project_id,
            mode=r.mode or "employee",
            status=r.status or "running",
            issue_number=r.issue_number,
            turns=r.turns,
            employee_index=r.employee_index,
            concurrent_group_id=r.concurrent_group_id,
            model=r.model,
            branch=r.branch,
        )
        for r in runs
    ]

    # Fallback: check for running coordinator tasks that don't have Run records.
    # This covers the coordinated path where the Python coordinator spawns
    # employees via task_started events instead of employee_start events.
    if len(employees) <= 1:
        coord_result = await db.execute(
            select(CoordinatorTask).where(CoordinatorTask.status == "running")
        )
        coord_tasks = coord_result.scalars().all()

        # Only synthesize if we have coordinator tasks beyond what's already shown
        seen_indices = {e.employee_index for e in employees}
        for ct in coord_tasks:
            if ct.employee_index in seen_indices:
                continue
            seen_indices.add(ct.employee_index)

            # Find the project_id from the first run (they share the same project)
            project_id = employees[0].project_id if employees else None
            if not project_id:
                proj_result = await db.execute(
                    select(Project).where(Project.repo == ct.project_repo)
                )
                proj = proj_result.scalar_one_or_none()
                project_id = proj.id if proj else 0

            employees.append(ActiveEmployeeOut(
                run_id=ct.run_id,
                project_id=project_id,
                mode="employee",
                status="running",
                issue_number=ct.issue_number,
                turns=None,
                employee_index=ct.employee_index,
                concurrent_group_id=ct.run_id,
                model=None,
                branch=ct.branch,
            ))

    return employees


@router.get("/latest", response_model=Optional[RunOut])
async def get_latest_run(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Run).order_by(desc(Run.started_at)).limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="No runs found")
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}/full", response_model=RunFullContext)
async def get_run_full_context(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return unified run context: run + coordinator tasks + queue item + plan.

    This powers the unified Run Detail view (AC2) by fetching all related
    data in a single request instead of requiring 4+ separate API calls.
    """
    # 1. Fetch the run
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # 2. Fetch coordinator tasks for this run
    tasks_result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.run_id == run_id)
    )
    tasks = tasks_result.scalars().all()

    # 3. Fetch coordinator messages for this run
    msgs_result = await db.execute(
        select(CoordinatorMessage)
        .where(CoordinatorMessage.run_id == run_id)
        .order_by(CoordinatorMessage.created_at)
    )
    messages = msgs_result.scalars().all()

    # 4. Fetch related queue item (by run_id)
    queue_result = await db.execute(
        select(QueueItem).where(QueueItem.run_id == run_id)
    )
    queue_item = queue_result.scalar_one_or_none()

    # 5. Fetch related plan (by implementation_run_id or run_id)
    plan_result = await db.execute(
        select(Plan).where(
            (Plan.implementation_run_id == run_id) | (Plan.run_id == run_id)
        )
    )
    plan = plan_result.scalar_one_or_none()

    # 6. Get project repo name
    project_repo: str | None = None
    if run.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_repo = project.repo

    # 7. Fetch intelligence decisions (agent events with intelligence.* type)
    intel_result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.run_id == run_id)
        .where(AgentEvent.event_type.like("intelligence.%"))
        .order_by(AgentEvent.created_at.asc())
    )
    intel_events = intel_result.scalars().all()

    return RunFullContext(
        run=RunOut.model_validate(run),
        coordinator_tasks=[CoordinatorTaskOut.model_validate(t) for t in tasks],
        coordinator_messages=[CoordinatorMessageOut.model_validate(m) for m in messages],
        queue_item=QueueItemOut.model_validate(queue_item) if queue_item else None,
        plan=PlanOut.model_validate(plan) if plan else None,
        project_repo=project_repo,
        intelligence_decisions=[AgentEventOut.model_validate(e) for e in intel_events],
    )


@router.get("/{run_id}/diff", response_model=DiffResult)
async def get_run_diff(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return the git diff for a run's branch vs the base branch.

    Looks up the run to find its branch and project, then runs
    `git diff <base_branch>...<employee_branch>` in the project workspace.
    Returns a structured diff with per-file hunks and line data.
    """
    # 1. Look up the run
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # No branch means no diff (e.g., analyze mode)
    if not run.branch:
        return DiffResult()

    # 2. Look up the project to get workspace path and base branch
    base_branch = "main"
    repo_name: str | None = None

    if run.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            base_branch = project.branch or "main"
            repo_name = project.repo.split("/")[-1] if "/" in project.repo else project.repo

    if not repo_name:
        # Try to infer from the employee report or return empty
        return DiffResult()

    # 3. Compute the workspace path
    workspace = Path(settings.workspaces_dir) / repo_name
    if not workspace.is_dir():
        return DiffResult()

    # 4. Check if the branch exists
    try:
        check_branch = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace), "rev-parse", "--verify", run.branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(check_branch.communicate(), timeout=30.0)
        except TimeoutError:
            check_branch.kill()
            await check_branch.wait()
            logger.warning("git rev-parse timed out for run %s", run_id)
            return DiffResult()
        if check_branch.returncode != 0:
            return DiffResult()
    except Exception:
        return DiffResult()

    # 5. Run git diff
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace), "diff",
            f"{base_branch}...{run.branch}", "--",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("git diff timed out for run %s", run_id)
            return DiffResult()

        if proc.returncode != 0:
            logger.warning("git diff failed for run %s: %s", run_id, stderr.decode())
            return DiffResult()

        diff_text = stdout.decode(errors="replace")
    except Exception as exc:
        logger.error("Error running git diff for run %s: %s", run_id, exc)
        return DiffResult()

    # 6. Parse and return
    return parse_unified_diff(diff_text)


@router.post("/rescan")
async def rescan_logs(db: AsyncSession = Depends(get_db)):
    """Manually trigger a re-scan of log files to import new runs."""
    imported = await import_historical_runs(db)
    return {"status": "ok", "imported": imported}


@router.post("/trigger")
async def trigger_run():
    """Trigger the agent service immediately."""
    result = await systemctl("start", "claude-agent.service")
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or result.get("stderr", "Failed to trigger run"),
        )
    return {"status": "triggered", "detail": "claude-agent.service started"}
