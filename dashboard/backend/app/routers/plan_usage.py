"""Plan usage detection and history API endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import PlanUsageHistory, Run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plan-usage", tags=["plan-usage"])


# --- Schemas ---

class ModelUsageOut(BaseModel):
    model: str
    tokens_used: int = 0
    tokens_limit: int = 0
    usage_percent: float = 0.0


class PlanUsageOut(BaseModel):
    """Current plan usage snapshot."""
    timestamp: str
    detection_method: str = "heuristic"
    plan_tier: str = "unknown"
    # Session
    session_tokens_used: int = 0
    session_tokens_limit: int = 0
    session_usage_percent: float = 0.0
    # Weekly
    weekly_tokens_used: int = 0
    weekly_tokens_limit: int = 0
    weekly_usage_percent: float = 0.0
    weekly_reset_at: str = ""
    # Per model
    per_model: list[ModelUsageOut] = []
    # Status
    is_throttled: bool = False
    should_throttle: bool = False
    throttle_reason: str = ""
    error: str | None = None


class PlanUsageHistoryOut(BaseModel):
    id: int
    timestamp: str
    detection_method: str | None = None
    plan_tier: str | None = None
    weekly_tokens_used: int = 0
    weekly_usage_percent: float = 0.0
    is_throttled: bool = False
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# Known plan tier token limits (weekly) — mirrors detect_plan_usage.py
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "max_5x": {
        "claude-opus-4-6": 225_000_000,
        "claude-sonnet-4-6": 900_000_000,
        "claude-haiku-4-5-20251001": 4_500_000_000,
        "default": 900_000_000,
    },
    "pro": {
        "claude-opus-4-6": 45_000_000,
        "claude-sonnet-4-6": 180_000_000,
        "claude-haiku-4-5-20251001": 900_000_000,
        "default": 180_000_000,
    },
    "team": {
        "claude-opus-4-6": 90_000_000,
        "claude-sonnet-4-6": 360_000_000,
        "claude-haiku-4-5-20251001": 1_800_000_000,
        "default": 360_000_000,
    },
}

DEFAULT_WEEKLY_LIMIT = 180_000_000


def _get_week_boundaries() -> tuple[datetime, datetime]:
    """Get current week start (Monday 00:00 UTC) and next reset time."""
    now = datetime.now(timezone.utc)
    days_since_monday = now.weekday()
    week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_reset = week_start + timedelta(days=7)
    return week_start, next_reset


@router.get("", response_model=PlanUsageOut)
async def get_plan_usage(
    plan_tier: str = Query("max_5x", description="Plan tier for limit calculation"),
    max_usage_percent: float = Query(85.0, description="Threshold for throttle warning"),
    db: AsyncSession = Depends(get_db),
) -> PlanUsageOut:
    """Get current plan usage based on tracked token consumption.

    Returns session-level, weekly, and per-model usage percentages,
    plus throttle recommendation.
    """
    now = datetime.now(timezone.utc)
    week_start, next_reset = _get_week_boundaries()

    tier_limits = PLAN_LIMITS.get(plan_tier, PLAN_LIMITS.get("pro", {}))
    default_limit = tier_limits.get("default", DEFAULT_WEEKLY_LIMIT)

    # Weekly aggregate usage from runs table
    weekly_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
        ).where(
            Run.started_at >= week_start.isoformat(),
            Run.status.isnot(None),
        )
    )
    weekly_row = weekly_result.one()
    weekly_tokens = weekly_row.input_tokens + weekly_row.output_tokens
    weekly_pct = (weekly_tokens / default_limit * 100.0) if default_limit > 0 else 0.0

    # Per-model breakdown
    model_result = await db.execute(
        select(
            Run.model,
            func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
        ).where(
            Run.started_at >= week_start.isoformat(),
            Run.model.isnot(None),
            Run.status.isnot(None),
        ).group_by(Run.model)
    )
    per_model = []
    any_model_throttled = False
    for mrow in model_result.all():
        tokens = mrow.input_tokens + mrow.output_tokens
        model_limit = tier_limits.get(mrow.model, default_limit)
        pct = (tokens / model_limit * 100.0) if model_limit > 0 else 0.0
        if pct >= max_usage_percent:
            any_model_throttled = True
        per_model.append(ModelUsageOut(
            model=mrow.model,
            tokens_used=tokens,
            tokens_limit=model_limit,
            usage_percent=round(pct, 2),
        ))

    # Session usage (current running runs)
    session_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
        ).where(Run.status == "running")
    )
    session_row = session_result.one()
    session_tokens = session_row.input_tokens + session_row.output_tokens
    session_limit = default_limit // 7  # Daily-equivalent
    session_pct = (session_tokens / session_limit * 100.0) if session_limit > 0 else 0.0

    # Throttle decision
    should_throttle = False
    throttle_reason = ""
    if weekly_pct >= max_usage_percent:
        should_throttle = True
        throttle_reason = (
            f"Weekly usage at {weekly_pct:.1f}% (threshold: {max_usage_percent:.1f}%)"
        )
    elif any_model_throttled:
        should_throttle = True
        high_models = [m for m in per_model if m.usage_percent >= max_usage_percent]
        throttle_reason = (
            "Model(s) near limit: "
            + ", ".join(f"{m.model} at {m.usage_percent:.1f}%" for m in high_models)
        )

    return PlanUsageOut(
        timestamp=now.isoformat(),
        detection_method="heuristic",
        plan_tier=plan_tier,
        session_tokens_used=session_tokens,
        session_tokens_limit=session_limit,
        session_usage_percent=round(session_pct, 2),
        weekly_tokens_used=weekly_tokens,
        weekly_tokens_limit=default_limit,
        weekly_usage_percent=round(weekly_pct, 2),
        weekly_reset_at=next_reset.isoformat(),
        per_model=per_model,
        is_throttled=should_throttle and weekly_pct >= 95.0,
        should_throttle=should_throttle,
        throttle_reason=throttle_reason,
    )


@router.get("/history", response_model=list[PlanUsageHistoryOut])
async def get_plan_usage_history(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[PlanUsageHistoryOut]:
    """Get historical plan usage snapshots."""
    result = await db.execute(
        select(PlanUsageHistory)
        .order_by(PlanUsageHistory.created_at.desc())
        .limit(limit)
    )
    return [PlanUsageHistoryOut.model_validate(row) for row in result.scalars().all()]


@router.post("/snapshot")
async def record_usage_snapshot(
    plan_tier: str = Query("max_5x"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a usage snapshot to history (called periodically or on-demand)."""
    now = datetime.now(timezone.utc)
    week_start, next_reset = _get_week_boundaries()

    tier_limits = PLAN_LIMITS.get(plan_tier, PLAN_LIMITS.get("pro", {}))
    default_limit = tier_limits.get("default", DEFAULT_WEEKLY_LIMIT)

    # Weekly aggregate
    weekly_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
        ).where(
            Run.started_at >= week_start.isoformat(),
            Run.status.isnot(None),
        )
    )
    weekly_row = weekly_result.one()
    weekly_tokens = weekly_row.input_tokens + weekly_row.output_tokens
    weekly_pct = (weekly_tokens / default_limit * 100.0) if default_limit > 0 else 0.0

    # Per-model
    model_result = await db.execute(
        select(
            Run.model,
            func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
        ).where(
            Run.started_at >= week_start.isoformat(),
            Run.model.isnot(None),
            Run.status.isnot(None),
        ).group_by(Run.model)
    )
    per_model_data = []
    for mrow in model_result.all():
        tokens = mrow.input_tokens + mrow.output_tokens
        model_limit = tier_limits.get(mrow.model, default_limit)
        pct = (tokens / model_limit * 100.0) if model_limit > 0 else 0.0
        per_model_data.append({
            "model": mrow.model,
            "tokens_used": tokens,
            "tokens_limit": model_limit,
            "usage_percent": round(pct, 2),
        })

    snapshot = PlanUsageHistory(
        timestamp=now.isoformat(),
        detection_method="heuristic",
        plan_tier=plan_tier,
        session_tokens_used=0,
        session_tokens_limit=0,
        session_usage_percent=0.0,
        weekly_tokens_used=weekly_tokens,
        weekly_tokens_limit=default_limit,
        weekly_usage_percent=round(weekly_pct, 2),
        weekly_reset_at=next_reset.isoformat(),
        per_model_json=json.dumps(per_model_data),
        is_throttled=weekly_pct >= 95.0,
    )
    db.add(snapshot)
    await db.commit()

    return {"status": "recorded", "weekly_usage_percent": round(weekly_pct, 2)}
