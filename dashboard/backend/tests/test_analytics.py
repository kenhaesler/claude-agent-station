"""Tests for the analytics API.

Covers:
- GET /api/analytics — aggregation with sample data
- Empty state returns zeros
- Date range filtering via days parameter
- Project filtering via project_id parameter
- Daily token usage, verdict distribution, project token usage, daily run counts
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, Run


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def sample_analytics_data(setup_db):
    """Insert projects and runs for analytics testing."""
    async with async_session() as session:
        project_a = Project(repo="owner/repo-a", priority="high", mode="full", enabled=True, branch="main")
        project_b = Project(repo="owner/repo-b", priority="low", mode="full", enabled=True, branch="main")
        session.add_all([project_a, project_b])
        await session.flush()

        now = datetime.now(timezone.utc)

        runs = [
            # Project A: 2 successful runs today
            Run(
                run_id="run-a1",
                project_id=project_a.id,
                status="success",
                verdict="APPROVE",
                tokens_input=30000,
                tokens_output=5000,
                tokens_total=35000,
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
            ),
            Run(
                run_id="run-a2",
                project_id=project_a.id,
                status="success",
                verdict="PR",
                tokens_input=20000,
                tokens_output=3000,
                tokens_total=23000,
                started_at=now - timedelta(hours=1),
                finished_at=now,
            ),
            # Project B: 1 failed run yesterday
            Run(
                run_id="run-b1",
                project_id=project_b.id,
                status="failed",
                verdict="REJECT",
                tokens_input=10000,
                tokens_output=2000,
                tokens_total=12000,
                started_at=now - timedelta(days=1, hours=3),
                finished_at=now - timedelta(days=1, hours=2),
            ),
            # Old run (45 days ago) — should be excluded with default 30-day window
            Run(
                run_id="run-old",
                project_id=project_a.id,
                status="success",
                verdict="APPROVE",
                tokens_input=5000,
                tokens_output=1000,
                tokens_total=6000,
                started_at=now - timedelta(days=45),
                finished_at=now - timedelta(days=45),
            ),
        ]
        session.add_all(runs)
        await session.commit()
        return {"project_a": project_a, "project_b": project_b}


# --- Empty state ---

@pytest.mark.asyncio
async def test_analytics_empty(client):
    """GET /api/analytics returns zeros when no data exists."""
    resp = await client.get("/api/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 0
    assert data["total_tokens"] == 0
    assert data["failed_runs"] == 0
    assert data["daily_token_usage"] == []
    assert data["verdict_distribution"] == []
    assert data["project_token_usage"] == []
    assert data["daily_run_counts"] == []


# --- With sample data ---

@pytest.mark.asyncio
async def test_analytics_totals(client, sample_analytics_data):
    """GET /api/analytics returns correct aggregate totals (30-day default window)."""
    resp = await client.get("/api/analytics")
    assert resp.status_code == 200
    data = resp.json()
    # 3 runs within 30 days (old run excluded)
    assert data["total_runs"] == 3
    assert data["total_tokens"] == 35000 + 23000 + 12000  # 70000
    assert data["total_tokens_input"] == 30000 + 20000 + 10000  # 60000
    assert data["total_tokens_output"] == 5000 + 3000 + 2000  # 10000
    assert data["failed_runs"] == 1


@pytest.mark.asyncio
async def test_analytics_verdict_distribution(client, sample_analytics_data):
    """GET /api/analytics includes verdict distribution."""
    resp = await client.get("/api/analytics")
    data = resp.json()
    verdicts = {v["verdict"]: v["count"] for v in data["verdict_distribution"]}
    assert verdicts.get("APPROVE") == 1
    assert verdicts.get("PR") == 1
    assert verdicts.get("REJECT") == 1


@pytest.mark.asyncio
async def test_analytics_project_token_usage(client, sample_analytics_data):
    """GET /api/analytics includes per-project token usage."""
    resp = await client.get("/api/analytics")
    data = resp.json()
    assert len(data["project_token_usage"]) == 2
    # Sorted by tokens_total desc — repo-a should be first
    project_repos = [p["project_repo"] for p in data["project_token_usage"]]
    assert project_repos[0] == "owner/repo-a"


@pytest.mark.asyncio
async def test_analytics_daily_run_counts(client, sample_analytics_data):
    """GET /api/analytics includes daily run counts with success/failed breakdown."""
    resp = await client.get("/api/analytics")
    data = resp.json()
    assert len(data["daily_run_counts"]) >= 1
    # Find the day with the failed run
    total_failed = sum(d["failed"] for d in data["daily_run_counts"])
    total_success = sum(d["success"] for d in data["daily_run_counts"])
    assert total_failed == 1
    assert total_success == 2


# --- Days parameter ---

@pytest.mark.asyncio
async def test_analytics_wider_window_includes_old_run(client, sample_analytics_data):
    """GET /api/analytics?days=60 includes runs older than 30 days."""
    resp = await client.get("/api/analytics?days=60")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 4  # includes the 45-day-old run


@pytest.mark.asyncio
async def test_analytics_narrow_window(client, sample_analytics_data):
    """GET /api/analytics?days=1 may exclude yesterday's run depending on exact timing."""
    # With days=1, the cutoff is 24h ago — yesterday's run may or may not be included
    resp = await client.get("/api/analytics?days=1")
    assert resp.status_code == 200
    data = resp.json()
    # At minimum the 2 runs from "today" should be included
    assert data["total_runs"] >= 2


# --- Project filter ---

@pytest.mark.asyncio
async def test_analytics_filter_by_project(client, sample_analytics_data):
    """GET /api/analytics?project_id=... filters to single project."""
    pid = sample_analytics_data["project_a"].id
    resp = await client.get(f"/api/analytics?project_id={pid}")
    assert resp.status_code == 200
    data = resp.json()
    # Project A has 2 runs in the 30-day window
    assert data["total_runs"] == 2
    assert data["failed_runs"] == 0
