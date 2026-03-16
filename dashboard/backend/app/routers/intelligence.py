"""Intelligence API: insights, outcomes, and decision data."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import AgentEvent, TaskOutcome
from app.schemas import TaskOutcomeCreate, TaskOutcomeOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/insights")
async def get_insights(db: AsyncSession = Depends(get_db)):
    """Return intelligence learning loop insights.

    Includes:
    - Success rates by mode/model combination
    - Confidence calibration (reported vs actual)
    - Token efficiency trends
    - Sample counts
    """
    # Success rates by mode/model
    mode_result = await db.execute(
        text("""
            SELECT mode_used, model_used,
                   COUNT(*) as total,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                   AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate,
                   AVG(tokens_consumed) as avg_tokens,
                   AVG(duration_seconds) as avg_duration
            FROM task_outcomes
            GROUP BY mode_used, model_used
            ORDER BY total DESC
        """)
    )
    success_rates = [
        {
            "mode": row.mode_used,
            "model": row.model_used,
            "total": row.total,
            "successes": row.successes,
            "success_rate": round(row.success_rate, 3) if row.success_rate else 0,
            "avg_tokens": int(row.avg_tokens) if row.avg_tokens else None,
            "avg_duration": int(row.avg_duration) if row.avg_duration else None,
        }
        for row in mode_result.all()
    ]

    # Confidence calibration: compare reported confidence vs actual success
    cal_result = await db.execute(
        text("""
            SELECT
                CASE
                    WHEN confidence_reported < 0.5 THEN '0.0-0.5'
                    WHEN confidence_reported < 0.7 THEN '0.5-0.7'
                    WHEN confidence_reported < 0.85 THEN '0.7-0.85'
                    WHEN confidence_reported < 0.95 THEN '0.85-0.95'
                    ELSE '0.95-1.0'
                END as confidence_bucket,
                COUNT(*) as total,
                AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as actual_success_rate,
                AVG(confidence_reported) as avg_reported_confidence
            FROM task_outcomes
            WHERE confidence_reported IS NOT NULL
            GROUP BY confidence_bucket
            ORDER BY avg_reported_confidence
        """)
    )
    calibration = [
        {
            "bucket": row.confidence_bucket,
            "total": row.total,
            "actual_success_rate": round(row.actual_success_rate, 3),
            "avg_reported_confidence": round(row.avg_reported_confidence, 3),
        }
        for row in cal_result.all()
    ]

    # Token efficiency: avg tokens per success by mode
    eff_result = await db.execute(
        text("""
            SELECT mode_used,
                   AVG(CASE WHEN success THEN tokens_consumed END) as avg_tokens_success,
                   AVG(CASE WHEN NOT success THEN tokens_consumed END) as avg_tokens_failure,
                   COUNT(*) as total
            FROM task_outcomes
            WHERE tokens_consumed IS NOT NULL
            GROUP BY mode_used
            ORDER BY total DESC
        """)
    )
    token_efficiency = [
        {
            "mode": row.mode_used,
            "avg_tokens_success": int(row.avg_tokens_success) if row.avg_tokens_success else None,
            "avg_tokens_failure": int(row.avg_tokens_failure) if row.avg_tokens_failure else None,
            "total": row.total,
        }
        for row in eff_result.all()
    ]

    # Escalation stats
    esc_result = await db.execute(
        text("""
            SELECT escalation_rung,
                   COUNT(*) as total,
                   AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate
            FROM task_outcomes
            GROUP BY escalation_rung
            ORDER BY escalation_rung
        """)
    )
    escalation_stats = [
        {
            "rung": row.escalation_rung,
            "total": row.total,
            "success_rate": round(row.success_rate, 3),
        }
        for row in esc_result.all()
    ]

    # Total sample count
    total_result = await db.execute(
        select(func.count(TaskOutcome.id))
    )
    total_samples = total_result.scalar() or 0

    # Recent intelligence events count
    intel_events_result = await db.execute(
        select(func.count(AgentEvent.event_id)).where(
            AgentEvent.event_type.like("intelligence.%")
        )
    )
    intel_event_count = intel_events_result.scalar() or 0

    return {
        "success_rates": success_rates,
        "calibration": calibration,
        "token_efficiency": token_efficiency,
        "escalation_stats": escalation_stats,
        "total_samples": total_samples,
        "intelligence_event_count": intel_event_count,
    }


@router.post("/outcomes", status_code=201, response_model=TaskOutcomeOut)
async def record_outcome(
    data: TaskOutcomeCreate,
    db: AsyncSession = Depends(get_db),
):
    """Record a task outcome for the learning loop."""
    outcome = TaskOutcome(
        queue_item_id=data.queue_item_id,
        project_repo=data.project_repo,
        issue_number=data.issue_number,
        issue_type=data.issue_type,
        complexity_score=data.complexity_score,
        mode_used=data.mode_used,
        model_used=data.model_used,
        escalation_rung=data.escalation_rung,
        prompt_version=data.prompt_version,
        confidence_reported=data.confidence_reported,
        success=data.success,
        tests_passed=data.tests_passed,
        verdict=data.verdict,
        failure_category=data.failure_category,
        subsystem=data.subsystem,
        employee_index=data.employee_index,
        tokens_consumed=data.tokens_consumed,
        duration_seconds=data.duration_seconds,
    )
    db.add(outcome)
    await db.commit()
    await db.refresh(outcome)

    logger.info(
        "Outcome recorded: %s mode=%s verdict=%s success=%s",
        data.project_repo, data.mode_used, data.verdict, data.success,
    )
    return TaskOutcomeOut.model_validate(outcome)


@router.get("/decisions")
async def list_intelligence_decisions(
    run_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List recent intelligence decisions (filtered agent events)."""
    q = select(AgentEvent).where(
        AgentEvent.event_type.like("intelligence.%")
    )
    if run_id:
        q = q.where(AgentEvent.run_id == run_id)
    q = q.order_by(AgentEvent.created_at.desc()).limit(limit)

    result = await db.execute(q)
    events = result.scalars().all()
    return [
        {
            "event_id": e.event_id,
            "workflow_id": e.workflow_id,
            "run_id": e.run_id,
            "event_type": e.event_type,
            "event_data": e.event_data,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
