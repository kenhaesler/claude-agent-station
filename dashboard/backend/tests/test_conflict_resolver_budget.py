"""Tests for the rolling 24h budget query."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, engine, async_session
from app.models import ConflictResolution
from agent.conflict_resolver.budget import (
    tokens_used_in_window,
    record_attempt_start,
    record_attempt_finish,
)


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_no_rows_returns_zero(setup_db):
    async with async_session() as db:
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 0


@pytest.mark.asyncio
async def test_sums_only_within_window(setup_db):
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        # 23h ago — counts
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r",
            started_at=now - timedelta(hours=23),
            finished_at=now - timedelta(hours=23),
            phase_reached="llm", outcome="resolved",
            tokens_total=5000, triggered_by="pre_pr",
        ))
        # 25h ago — does not count
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r",
            started_at=now - timedelta(hours=25),
            finished_at=now - timedelta(hours=25),
            phase_reached="llm", outcome="resolved",
            tokens_total=99999, triggered_by="pre_pr",
        ))
        await db.commit()
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 5000


@pytest.mark.asyncio
async def test_only_counts_matching_branch(setup_db):
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r", started_at=now,
            phase_reached="llm", outcome="resolved",
            tokens_total=1000, triggered_by="pre_pr",
        ))
        db.add(ConflictResolution(
            branch="feature/y", repo="owner/r", started_at=now,
            phase_reached="llm", outcome="resolved",
            tokens_total=99999, triggered_by="pre_pr",
        ))
        await db.commit()
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 1000


@pytest.mark.asyncio
async def test_record_start_returns_attempt_id(setup_db):
    async with async_session() as db:
        attempt_id = await record_attempt_start(
            db, branch="feature/x", repo="owner/r",
            triggered_by="pre_pr", run_id="run-test-001",
        )
    assert isinstance(attempt_id, int)
    assert attempt_id > 0


@pytest.mark.asyncio
async def test_record_finish_updates_row(setup_db):
    async with async_session() as db:
        attempt_id = await record_attempt_start(
            db, branch="feature/x", repo="owner/r",
            triggered_by="pre_pr", run_id="run-test-002",
        )
        await record_attempt_finish(
            db, attempt_id=attempt_id,
            phase_reached="llm", outcome="resolved",
            tokens_input=1000, tokens_output=500, tokens_total=1500,
            model_used="claude-opus-4-7", feedback_rounds=1,
        )
        row = (await db.execute(
            select(ConflictResolution).where(ConflictResolution.id == attempt_id)
        )).scalar_one()
    assert row.outcome == "resolved"
    assert row.tokens_total == 1500
    assert row.finished_at is not None
