"""Analytics endpoints for run statistics and token usage charts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import AgentEvent, Project, Run
from app.schemas import (
    AnalyticsResponse,
    DailyRunCount,
    DailyTokenUsage,
    ProjectTokenUsage,
    VerdictDistribution,
)

AUTONOMY_EVENT_TYPES = ("auto_mode_decision", "auto_mode_referral")

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


# --- Autonomy audit + analytics (P3.T8, P3.T9) -----------------------------


def _parse_event_data(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _decision_from_event(event_type: str, data: dict[str, Any]) -> str:
    """Normalise the decision outcome across both event types.

    `auto_mode_decision` stores 'allow' / 'deny' directly.
    `auto_mode_referral` stores `final_status` ∈ {approved, denied, timed_out,
    post_failed}. Approved maps to 'allow'; anything else to 'deny'.
    """
    if event_type == "auto_mode_decision":
        return str(data.get("decision", "unknown"))
    final = str(data.get("final_status", ""))
    return "allow" if final == "approved" else "deny"


@router.get("/autonomy-audit")
async def get_autonomy_audit(
    run_id: str | None = Query(None, description="Filter to one run"),
    tool_name: str | None = Query(None, description="Filter by tool name"),
    decision: str | None = Query(None, description="Filter allow/deny"),
    event_type: str | None = Query(None, description="Filter auto_mode_decision or auto_mode_referral"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Raw audit trail of policy-engine decisions.

    Powers the Autonomy Audit subpage. Returns newest-first so operators
    land on the latest decisions without pagination. The decision field is
    normalised across `auto_mode_decision` and `auto_mode_referral` rows.
    """
    types = [event_type] if event_type in AUTONOMY_EVENT_TYPES else list(AUTONOMY_EVENT_TYPES)

    stmt = select(AgentEvent).where(AgentEvent.event_type.in_(types))
    if run_id:
        stmt = stmt.where(AgentEvent.run_id == run_id)
    stmt = stmt.order_by(AgentEvent.created_at.desc())

    # We post-filter on event_data (JSON) because it's stored as TEXT — SQLite
    # doesn't have native JSON ops pre-3.45 across all deployments. For
    # pragmatic scale (audit rows are bounded by runs-per-day), over-fetch a
    # window and filter in Python; if this ever pressures the DB we'll promote
    # decision/tool_name to first-class columns.
    result = await db.execute(stmt.limit(max(limit + offset, 1) * 4))
    rows = list(result.scalars().all())

    items: list[dict[str, Any]] = []
    for row in rows:
        data = _parse_event_data(row.event_data)
        row_decision = _decision_from_event(row.event_type, data)
        row_tool = str(data.get("tool_name", ""))
        if tool_name and row_tool != tool_name:
            continue
        if decision and row_decision != decision:
            continue
        items.append({
            "event_id": row.event_id,
            "event_type": row.event_type,
            "workflow_id": row.workflow_id,
            "run_id": row.run_id,
            "agent_id": row.agent_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "tool_name": row_tool,
            "decision": row_decision,
            "level": data.get("level"),
            "reason": data.get("reason"),
            "request_id": data.get("request_id"),
            "tool_input": data.get("tool_input") or {},
        })

    total = len(items)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


@router.get("/autonomy")
async def get_autonomy_summary(
    days: int = Query(30, ge=1, le=365, description="Window in days"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Aggregated policy-engine decision counts for the donut chart.

    Returns counts by autonomy level, by decision outcome, by tool, plus a
    `by_level_decision` cross-tab used to drive the donut + legend.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(AgentEvent)
        .where(
            AgentEvent.event_type.in_(AUTONOMY_EVENT_TYPES),
            AgentEvent.created_at >= cutoff,
        )
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    by_level: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    by_level_decision: dict[str, dict[str, int]] = {}
    by_event_type: dict[str, int] = {t: 0 for t in AUTONOMY_EVENT_TYPES}

    for row in rows:
        data = _parse_event_data(row.event_data)
        level = str(data.get("level", "unknown"))
        decision = _decision_from_event(row.event_type, data)
        tool = str(data.get("tool_name", "unknown"))

        by_level[level] = by_level.get(level, 0) + 1
        by_decision[decision] = by_decision.get(decision, 0) + 1
        by_tool[tool] = by_tool.get(tool, 0) + 1
        by_event_type[row.event_type] = by_event_type.get(row.event_type, 0) + 1

        bucket = by_level_decision.setdefault(level, {})
        bucket[decision] = bucket.get(decision, 0) + 1

    return {
        "days": days,
        "total_decisions": len(rows),
        "by_level": by_level,
        "by_decision": by_decision,
        "by_tool": dict(sorted(by_tool.items(), key=lambda x: x[1], reverse=True)[:10]),
        "by_level_decision": by_level_decision,
        "by_event_type": by_event_type,
    }
