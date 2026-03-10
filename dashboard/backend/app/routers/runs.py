"""Run history endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Run
from app.schemas import RunOut, RunList, ActiveEmployeeOut
from app.services.log_importer import import_historical_runs
from app.services.systemd import systemctl

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
