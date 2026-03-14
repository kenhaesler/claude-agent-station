"""Adaptive scheduling: uses historical outcomes to predict optimal config."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MIN_SAMPLES = 3  # Minimum outcomes needed before predictions are trusted


@dataclass
class EffortPrediction:
    """Predicted optimal configuration based on historical outcomes."""
    mode: str
    model: str
    predicted_tokens: float | None
    predicted_duration: float | None
    confidence: float  # Historical success rate
    sample_count: int


async def predict_effort(
    issue_type: str,
    complexity: int,
    db: AsyncSession,
) -> EffortPrediction | None:
    """Predict optimal mode/model config based on historical outcomes.

    Returns None if insufficient data (< MIN_SAMPLES matching outcomes).
    """
    result = await db.execute(
        text("""
            SELECT mode_used, model_used,
                   AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate,
                   AVG(tokens_consumed) as avg_tokens,
                   AVG(duration_seconds) as avg_duration,
                   COUNT(*) as sample_count
            FROM task_outcomes
            WHERE issue_type = :type AND complexity_score = :complexity
            GROUP BY mode_used, model_used
            HAVING COUNT(*) >= :min_samples
            ORDER BY success_rate DESC, avg_tokens ASC
            LIMIT 1
        """),
        {"type": issue_type, "complexity": complexity, "min_samples": MIN_SAMPLES},
    )

    best = result.first()
    if not best or best.success_rate < 0.7:
        return None

    return EffortPrediction(
        mode=best.mode_used,
        model=best.model_used,
        predicted_tokens=best.avg_tokens,
        predicted_duration=best.avg_duration,
        confidence=best.success_rate,
        sample_count=best.sample_count,
    )


def predict_effort_sync(
    issue_type: str,
    complexity: int,
    db_path: str = "/var/lib/claude-agent-station/station.db",
) -> EffortPrediction | None:
    """Synchronous version of predict_effort for CLI use (no async).

    Uses plain sqlite3 instead of SQLAlchemy async session.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT mode_used, model_used,
                   AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate,
                   AVG(tokens_consumed) as avg_tokens,
                   AVG(duration_seconds) as avg_duration,
                   COUNT(*) as sample_count
            FROM task_outcomes
            WHERE issue_type = ? AND complexity_score = ?
            GROUP BY mode_used, model_used
            HAVING COUNT(*) >= ?
            ORDER BY success_rate DESC, avg_tokens ASC
            LIMIT 1
            """,
            (issue_type, complexity, MIN_SAMPLES),
        )
        row = cursor.fetchone()
        conn.close()

        if not row or row["success_rate"] < 0.7:
            return None

        return EffortPrediction(
            mode=row["mode_used"],
            model=row["model_used"],
            predicted_tokens=row["avg_tokens"],
            predicted_duration=row["avg_duration"],
            confidence=row["success_rate"],
            sample_count=row["sample_count"],
        )
    except Exception as e:
        logger.warning("predict_effort_sync failed: %s", e)
        return None


async def get_project_success_rates(
    project_repo: str,
    db: AsyncSession,
) -> dict:
    """Get success rates by mode for a project."""
    result = await db.execute(
        text("""
            SELECT mode_used,
                   COUNT(*) as total,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                   AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate,
                   AVG(tokens_consumed) as avg_tokens
            FROM task_outcomes
            WHERE project_repo = :repo
            GROUP BY mode_used
            ORDER BY total DESC
        """),
        {"repo": project_repo},
    )

    rates = {}
    for row in result.all():
        rates[row.mode_used] = {
            "total": row.total,
            "successes": row.successes,
            "success_rate": round(row.success_rate, 3),
            "avg_tokens": int(row.avg_tokens) if row.avg_tokens else None,
        }
    return rates
