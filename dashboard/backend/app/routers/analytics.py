"""Analytics endpoints for run statistics and token usage charts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project, Run
from app.schemas import (
    AnalyticsResponse,
    DailyRunCount,
    DailyTokenUsage,
    ProjectTokenUsage,
    VerdictDistribution,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    project_id: int | None = Query(None, description="Filter by project ID"),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    """Aggregate run statistics for charts.

    Returns daily token usage, verdict distribution, per-project token totals,
    and daily run frequency for the specified time window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Base filter conditions
    base_conditions = [Run.started_at >= cutoff]
    if project_id is not None:
        base_conditions.append(Run.project_id == project_id)

    # 1. Daily token usage (GROUP BY date)
    daily_tokens_query = (
        select(
            func.date(Run.started_at).label("date"),
            func.coalesce(func.sum(Run.tokens_total), 0).label("tokens_total"),
            func.coalesce(func.sum(Run.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("tokens_output"),
            func.count(Run.id).label("run_count"),
        )
        .where(and_(*base_conditions))
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
    )
    daily_tokens_result = await db.execute(daily_tokens_query)
    daily_tokens_rows = daily_tokens_result.all()

    daily_token_usage = [
        DailyTokenUsage(
            date=row.date,
            tokens_total=row.tokens_total,
            tokens_input=row.tokens_input,
            tokens_output=row.tokens_output,
            run_count=row.run_count,
        )
        for row in daily_tokens_rows
    ]

    # 2. Verdict distribution (GROUP BY verdict)
    verdict_query = (
        select(
            func.coalesce(Run.verdict, "none").label("verdict"),
            func.count(Run.id).label("count"),
        )
        .where(and_(*base_conditions))
        .group_by(func.coalesce(Run.verdict, "none"))
    )
    verdict_result = await db.execute(verdict_query)
    verdict_rows = verdict_result.all()

    # Also count failed runs separately (status='failed' regardless of verdict)
    failed_query = (
        select(func.count(Run.id))
        .where(and_(*base_conditions, Run.status == "failed"))
    )
    failed_result = await db.execute(failed_query)
    failed_count = failed_result.scalar() or 0

    verdict_distribution = [
        VerdictDistribution(verdict=row.verdict, count=row.count)
        for row in verdict_rows
    ]

    # 3. Tokens per project (GROUP BY project_id, joined with project name)
    project_tokens_query = (
        select(
            Run.project_id,
            Project.repo.label("project_repo"),
            func.coalesce(func.sum(Run.tokens_total), 0).label("tokens_total"),
            func.coalesce(func.sum(Run.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("tokens_output"),
            func.count(Run.id).label("run_count"),
        )
        .outerjoin(Project, Run.project_id == Project.id)
        .where(and_(*base_conditions, Run.project_id.isnot(None)))
        .group_by(Run.project_id, Project.repo)
        .order_by(func.sum(Run.tokens_total).desc())
        .limit(10)
    )
    project_tokens_result = await db.execute(project_tokens_query)
    project_tokens_rows = project_tokens_result.all()

    project_token_usage = [
        ProjectTokenUsage(
            project_id=row.project_id,
            project_repo=row.project_repo or f"project-{row.project_id}",
            tokens_total=row.tokens_total,
            tokens_input=row.tokens_input,
            tokens_output=row.tokens_output,
            run_count=row.run_count,
        )
        for row in project_tokens_rows
    ]

    # 4. Daily run counts (GROUP BY date)
    daily_runs_query = (
        select(
            func.date(Run.started_at).label("date"),
            func.count(Run.id).label("total"),
            func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
            func.sum(case((Run.status == "failed", 1), else_=0)).label("failed"),
        )
        .where(and_(*base_conditions))
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
    )
    daily_runs_result = await db.execute(daily_runs_query)
    daily_runs_rows = daily_runs_result.all()

    daily_run_counts = [
        DailyRunCount(
            date=row.date,
            total=row.total,
            success=row.success or 0,
            failed=row.failed or 0,
        )
        for row in daily_runs_rows
    ]

    # Summary totals
    total_tokens_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_total), 0),
            func.coalesce(func.sum(Run.tokens_input), 0),
            func.coalesce(func.sum(Run.tokens_output), 0),
            func.count(Run.id),
        ).where(and_(*base_conditions))
    )
    summary_row = total_tokens_result.one()

    return AnalyticsResponse(
        days=days,
        total_tokens=summary_row[0],
        total_tokens_input=summary_row[1],
        total_tokens_output=summary_row[2],
        total_runs=summary_row[3],
        failed_runs=failed_count,
        daily_token_usage=daily_token_usage,
        verdict_distribution=verdict_distribution,
        project_token_usage=project_token_usage,
        daily_run_counts=daily_run_counts,
    )
