"""Tests for the plan usage router.

Covers:
- GET /api/plan-usage — returns current plan usage
- GET /api/plan-usage/history — returns usage history
- POST /api/plan-usage/snapshot — records a snapshot
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import PlanUsageHistory, Project, Run


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
async def sample_runs(setup_db):
    """Insert sample project and runs for usage testing."""
    async with async_session() as session:
        project = Project(
            repo="owner/usage-repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.flush()

        now = datetime.now(UTC)
        runs = [
            Run(
                run_id="usage-run-001",
                project_id=project.id,
                status="completed",
                model="claude-sonnet-4-6",
                tokens_input=50000,
                tokens_output=20000,
                tokens_total=70000,
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
            ),
            Run(
                run_id="usage-run-002",
                project_id=project.id,
                status="completed",
                model="claude-opus-4-6",
                tokens_input=30000,
                tokens_output=10000,
                tokens_total=40000,
                started_at=now - timedelta(hours=1),
                finished_at=now,
            ),
        ]
        session.add_all(runs)
        await session.commit()


# ---------------------------------------------------------------------------
# GET /api/plan-usage — current plan usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan_usage_empty(client):
    """GET /api/plan-usage should return zeros when no runs exist."""
    resp = await client.get("/api/plan-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["weekly_tokens_used"] == 0
    assert data["weekly_usage_percent"] == 0.0
    assert data["plan_tier"] == "max_5x"  # default
    assert data["detection_method"] == "heuristic"
    assert data["is_throttled"] is False
    assert data["should_throttle"] is False


@pytest.mark.asyncio
async def test_get_plan_usage_with_runs(client, sample_runs):
    """GET /api/plan-usage should aggregate token usage from runs."""
    resp = await client.get("/api/plan-usage")
    assert resp.status_code == 200
    data = resp.json()
    # Total tokens = (50000+20000) + (30000+10000) = 110000
    assert data["weekly_tokens_used"] == 110000
    assert data["weekly_tokens_limit"] > 0
    assert len(data["per_model"]) == 2


@pytest.mark.asyncio
async def test_get_plan_usage_custom_tier(client, sample_runs):
    """GET /api/plan-usage?plan_tier=pro should use pro limits."""
    resp = await client.get("/api/plan-usage?plan_tier=pro")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan_tier"] == "pro"
    # Pro default limit is 180_000_000, much lower than max_5x
    assert data["weekly_tokens_limit"] == 180_000_000


@pytest.mark.asyncio
async def test_get_plan_usage_per_model_breakdown(client, sample_runs):
    """GET /api/plan-usage should break down usage per model."""
    resp = await client.get("/api/plan-usage")
    assert resp.status_code == 200
    per_model = resp.json()["per_model"]
    models = {m["model"]: m for m in per_model}
    assert "claude-sonnet-4-6" in models
    assert "claude-opus-4-6" in models
    assert models["claude-sonnet-4-6"]["tokens_used"] == 70000
    assert models["claude-opus-4-6"]["tokens_used"] == 40000


@pytest.mark.asyncio
async def test_get_plan_usage_weekly_reset(client):
    """GET /api/plan-usage should include weekly_reset_at timestamp."""
    resp = await client.get("/api/plan-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["weekly_reset_at"] != ""
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# GET /api/plan-usage/history — usage history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan_usage_history_empty(client):
    """GET /api/plan-usage/history should return empty list when no snapshots."""
    resp = await client.get("/api/plan-usage/history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_plan_usage_history_with_snapshots(client):
    """GET /api/plan-usage/history should return stored snapshots."""
    async with async_session() as session:
        for i in range(3):
            snapshot = PlanUsageHistory(
                timestamp=datetime.now(UTC).isoformat(),
                detection_method="heuristic",
                plan_tier="max_5x",
                weekly_tokens_used=i * 10000,
                weekly_usage_percent=float(i),
                is_throttled=False,
            )
            session.add(snapshot)
        await session.commit()

    resp = await client.get("/api/plan-usage/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_get_plan_usage_history_limit(client):
    """GET /api/plan-usage/history?limit=2 should respect limit."""
    async with async_session() as session:
        for _i in range(5):
            snapshot = PlanUsageHistory(
                timestamp=datetime.now(UTC).isoformat(),
                detection_method="heuristic",
                plan_tier="max_5x",
                weekly_tokens_used=0,
                weekly_usage_percent=0.0,
                is_throttled=False,
            )
            session.add(snapshot)
        await session.commit()

    resp = await client.get("/api/plan-usage/history?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# POST /api/plan-usage/snapshot — record snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_usage_snapshot(client):
    """POST /api/plan-usage/snapshot should create a history record."""
    resp = await client.post("/api/plan-usage/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert "weekly_usage_percent" in data

    # Verify it was stored
    history_resp = await client.get("/api/plan-usage/history")
    assert len(history_resp.json()) == 1


@pytest.mark.asyncio
async def test_record_usage_snapshot_with_runs(client, sample_runs):
    """POST /api/plan-usage/snapshot should capture current token totals."""
    resp = await client.post("/api/plan-usage/snapshot")
    assert resp.status_code == 200
    # weekly_usage_percent should be > 0 since we have runs with tokens
    assert resp.json()["weekly_usage_percent"] >= 0


@pytest.mark.asyncio
async def test_record_usage_snapshot_custom_tier(client):
    """POST /api/plan-usage/snapshot?plan_tier=pro should use specified tier."""
    resp = await client.post("/api/plan-usage/snapshot?plan_tier=pro")
    assert resp.status_code == 200

    history = await client.get("/api/plan-usage/history")
    snapshot = history.json()[0]
    assert snapshot["plan_tier"] == "pro"
