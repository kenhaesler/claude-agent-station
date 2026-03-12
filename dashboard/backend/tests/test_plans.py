"""Tests for Plan model timezone-aware timestamps.

Verifies that Plan.created_at and Plan.updated_at use timezone-aware
datetimes (via _utcnow), consistent with all other models.
"""

import pytest
import pytest_asyncio
from datetime import timezone

from app.database import engine, Base, async_session
from app.models import Project, Plan, _utcnow


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def sample_project(setup_db):
    """Insert a sample project for tests that need one."""
    async with async_session() as session:
        project = Project(
            repo="owner/plan-test-repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


def test_utcnow_returns_timezone_aware():
    """_utcnow() helper should return a timezone-aware datetime with UTC tzinfo."""
    dt = _utcnow()
    assert dt.tzinfo is not None, "_utcnow should return timezone-aware datetime"
    assert dt.tzinfo == timezone.utc


def test_plan_created_at_default_is_utcnow():
    """Plan.created_at column default should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.created_at
    assert col.default is not None
    assert col.default.arg.__name__ == "_utcnow", (
        f"Plan.created_at default should be _utcnow, got {col.default.arg.__name__}"
    )


def test_plan_updated_at_default_is_utcnow():
    """Plan.updated_at column default should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.updated_at
    assert col.default is not None
    assert col.default.arg.__name__ == "_utcnow", (
        f"Plan.updated_at default should be _utcnow, got {col.default.arg.__name__}"
    )


def test_plan_updated_at_onupdate_is_utcnow():
    """Plan.updated_at onupdate should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.updated_at
    assert col.onupdate is not None
    assert col.onupdate.arg.__name__ == "_utcnow", (
        f"Plan.updated_at onupdate should be _utcnow, got {col.onupdate.arg.__name__}"
    )


@pytest.mark.asyncio
async def test_plan_timestamps_consistent_with_project(sample_project):
    """Plan timestamps should be comparable with Project timestamps (both use same default)."""
    async with async_session() as session:
        plan = Plan(
            project_id=sample_project.id,
            title="Test plan",
            status="draft",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

        # Both should have timestamps set
        assert plan.created_at is not None
        assert plan.updated_at is not None
        assert sample_project.created_at is not None
