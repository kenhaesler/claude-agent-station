"""Tests for the conflict_resolutions table schema and indexes."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.database import Base, engine, async_session
from app.models import ConflictResolution


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_table_exists_with_required_columns(setup_db):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(conflict_resolutions)"))
        columns = {row[1] for row in result.fetchall()}
    required = {
        "id", "branch", "repo", "pr_number", "started_at", "finished_at",
        "phase_reached", "outcome", "tokens_input", "tokens_output",
        "tokens_total", "model_used", "feedback_rounds", "triggered_by",
        "run_id", "error_detail",
    }
    assert required.issubset(columns), f"missing columns: {required - columns}"


@pytest.mark.asyncio
async def test_index_on_branch_and_started_at(setup_db):
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='conflict_resolutions'"
        ))
        indexes = {row[0] for row in result.fetchall()}
    assert any("branch" in idx for idx in indexes), \
        f"expected an index covering 'branch', got: {indexes}"


@pytest.mark.asyncio
async def test_insert_and_query_minimal_row(setup_db):
    from datetime import datetime, timezone
    async with async_session() as db:
        row = ConflictResolution(
            branch="feature/x",
            repo="owner/repo",
            started_at=datetime.now(timezone.utc),
            phase_reached="mechanical",
            outcome="resolved",
            triggered_by="pre_pr",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        assert row.id is not None
