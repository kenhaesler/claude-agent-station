"""Budget query + attempt recording for the conflict resolver.

Pure functions over the conflict_resolutions table. The rolling 24h budget
is computed per-branch by summing tokens_total over rows whose started_at
falls in the window. See spec
docs/superpowers/specs/2026-05-10-conflict-resolution-design.md (Phase 0).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConflictResolution


async def tokens_used_in_window(
    db: AsyncSession,
    *,
    branch: str,
    window_hours: int = 24,
) -> int:
    """Sum tokens_total for `branch` over the rolling window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    result = await db.execute(
        select(func.coalesce(func.sum(ConflictResolution.tokens_total), 0))
        .where(ConflictResolution.branch == branch)
        .where(ConflictResolution.started_at >= cutoff)
    )
    return int(result.scalar() or 0)


async def record_attempt_start(
    db: AsyncSession,
    *,
    branch: str,
    repo: str,
    triggered_by: str,
    run_id: str | None = None,
    pr_number: int | None = None,
) -> int:
    """Insert a new in-flight attempt; return its id.

    finished_at, outcome, and token totals are filled in by record_attempt_finish.
    Defaults phase_reached='mechanical' so a crashed attempt isn't ambiguous —
    callers update it as they progress.
    """
    row = ConflictResolution(
        branch=branch,
        repo=repo,
        triggered_by=triggered_by,
        run_id=run_id,
        pr_number=pr_number,
        phase_reached="mechanical",
        outcome="error",  # default; finalize on success
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return int(row.id)


async def record_attempt_finish(
    db: AsyncSession,
    *,
    attempt_id: int,
    phase_reached: str,
    outcome: str,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    tokens_total: int | None = None,
    model_used: str | None = None,
    feedback_rounds: int = 0,
    error_detail: str | None = None,
) -> None:
    """Finalize an in-flight attempt."""
    row = (await db.execute(
        select(ConflictResolution).where(ConflictResolution.id == attempt_id)
    )).scalar_one()
    row.phase_reached = phase_reached
    row.outcome = outcome
    row.finished_at = datetime.now(timezone.utc)
    if tokens_input is not None:
        row.tokens_input = tokens_input
    if tokens_output is not None:
        row.tokens_output = tokens_output
    if tokens_total is not None:
        row.tokens_total = tokens_total
    if model_used is not None:
        row.model_used = model_used
    row.feedback_rounds = feedback_rounds
    if error_detail is not None:
        row.error_detail = error_detail
    await db.commit()
