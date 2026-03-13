"""Run history endpoints."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import Run, Project, CoordinatorTask, CoordinatorMessage, QueueItem, Plan
from app.schemas import (
    RunOut, RunList, ActiveEmployeeOut, RunFullContext,
    CoordinatorTaskOut, CoordinatorMessageOut, QueueItemOut, PlanOut,
)
from app.services.diff_parser import parse_unified_diff, DiffResult
from app.services.log_importer import import_historical_runs
from app.services.systemd import systemctl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=RunList)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    verdict: Optional[str] = None,
    concurrent_group_id: Optional[str] = None,
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
    """Return all currently running employee/agent runs for workspace visualization."""
    result = await db.execute(
        select(Run).where(Run.status == "running").where(Run.project_id.isnot(None))
    )
    runs = result.scalars().all()
    return [
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
    project_repo: Optional[str] = None
    if run.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_repo = project.repo

    return RunFullContext(
        run=RunOut.model_validate(run),
        coordinator_tasks=[CoordinatorTaskOut.model_validate(t) for t in tasks],
        coordinator_messages=[CoordinatorMessageOut.model_validate(m) for m in messages],
        queue_item=QueueItemOut.model_validate(queue_item) if queue_item else None,
        plan=PlanOut.model_validate(plan) if plan else None,
        project_repo=project_repo,
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
    repo_name: Optional[str] = None

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
        await check_branch.communicate()
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
        stdout, stderr = await proc.communicate()

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
