"""Tests for the runs API.

Covers:
- GET /api/runs — list with pagination and filters
- GET /api/runs/{run_id} — get single run
- GET /api/runs/latest — get most recent run
- GET /api/runs/active-employees — get running employees
- 404 for missing run
"""

import json
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import engine, Base, async_session
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
async def sample_runs(setup_db):
    """Insert sample project and runs for testing."""
    async with async_session() as session:
        project = Project(
            repo="owner/test-repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.flush()

        now = datetime.now(timezone.utc)
        runs = [
            Run(
                run_id="run-001",
                project_id=project.id,
                status="success",
                verdict="APPROVE",
                issue_number=1,
                branch="autonomous/issue-1",
                tokens_total=50000,
                tokens_input=40000,
                tokens_output=10000,
                turns=30,
                duration_ms=60000,
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2),
                employee_index=0,
            ),
            Run(
                run_id="run-002",
                project_id=project.id,
                status="failed",
                verdict="REJECT",
                issue_number=2,
                tokens_total=20000,
                started_at=now - timedelta(hours=1),
                finished_at=now,
                employee_index=0,
            ),
            Run(
                run_id="run-003",
                project_id=project.id,
                status="running",
                issue_number=3,
                started_at=now,
                employee_index=1,
                concurrent_group_id="group-A",
            ),
        ]
        session.add_all(runs)
        await session.commit()
        return {"project": project, "runs": runs}


# --- List runs ---

@pytest.mark.asyncio
async def test_list_runs_empty(client):
    """GET /api/runs returns empty list when no runs exist."""
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["runs"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_runs(client, sample_runs):
    """GET /api/runs returns all runs."""
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["runs"]) == 3


@pytest.mark.asyncio
async def test_list_runs_pagination(client, sample_runs):
    """GET /api/runs respects limit and offset."""
    resp = await client.get("/api/runs?limit=1&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3  # total remains the full count
    assert len(data["runs"]) == 1


@pytest.mark.asyncio
async def test_list_runs_filter_by_status(client, sample_runs):
    """GET /api/runs?status=success filters by status."""
    resp = await client.get("/api/runs?status=success")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["runs"][0]["run_id"] == "run-001"


@pytest.mark.asyncio
async def test_list_runs_filter_by_verdict(client, sample_runs):
    """GET /api/runs?verdict=REJECT filters by verdict."""
    resp = await client.get("/api/runs?verdict=REJECT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["runs"][0]["run_id"] == "run-002"


@pytest.mark.asyncio
async def test_list_runs_filter_by_project_id(client, sample_runs):
    """GET /api/runs?project_id=... filters by project."""
    pid = sample_runs["project"].id
    resp = await client.get(f"/api/runs?project_id={pid}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 3

    resp = await client.get("/api/runs?project_id=9999")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_runs_filter_by_concurrent_group(client, sample_runs):
    """GET /api/runs?concurrent_group_id=group-A filters."""
    resp = await client.get("/api/runs?concurrent_group_id=group-A")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["runs"][0]["run_id"] == "run-003"


@pytest.mark.asyncio
async def test_list_runs_ordered_by_started_at_desc(client, sample_runs):
    """Runs should be ordered by started_at descending (most recent first)."""
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    run_ids = [r["run_id"] for r in data["runs"]]
    # run-003 is most recent, run-001 is oldest
    assert run_ids == ["run-003", "run-002", "run-001"]


# --- Get single run ---

@pytest.mark.asyncio
async def test_get_run(client, sample_runs):
    """GET /api/runs/{run_id} returns the run."""
    resp = await client.get("/api/runs/run-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-001"
    assert data["status"] == "success"
    assert data["verdict"] == "APPROVE"
    assert data["tokens_total"] == 50000


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    """GET /api/runs/{run_id} returns 404 for unknown run."""
    resp = await client.get("/api/runs/nonexistent-run")
    assert resp.status_code == 404


# --- Latest run ---

@pytest.mark.asyncio
async def test_get_latest_run(client, sample_runs):
    """GET /api/runs/latest returns the most recent run."""
    resp = await client.get("/api/runs/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-003"  # Most recent by started_at


@pytest.mark.asyncio
async def test_get_latest_run_empty(client):
    """GET /api/runs/latest returns 404 when no runs exist."""
    resp = await client.get("/api/runs/latest")
    assert resp.status_code == 404


# --- Active employees ---

@pytest.mark.asyncio
async def test_get_active_employees(client, sample_runs):
    """GET /api/runs/active-employees returns only running runs."""
    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["run_id"] == "run-003"
    assert data[0]["status"] == "running"
    assert data[0]["issue_number"] == 3


@pytest.mark.asyncio
async def test_get_active_employees_empty(client):
    """GET /api/runs/active-employees returns empty list when no runs are active."""
    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    assert resp.json() == []


# --- Full context (unified run detail) ---

@pytest.mark.asyncio
async def test_get_run_full_context(client, sample_runs):
    """GET /api/runs/{run_id}/full returns run with related context."""
    resp = await client.get("/api/runs/run-001/full")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"]["run_id"] == "run-001"
    assert data["run"]["status"] == "success"
    assert data["project_repo"] == "owner/test-repo"
    assert isinstance(data["coordinator_tasks"], list)
    assert isinstance(data["coordinator_messages"], list)
    # No queue item or plan linked to this run
    assert data["queue_item"] is None
    assert data["plan"] is None


@pytest.mark.asyncio
async def test_get_run_full_context_not_found(client):
    """GET /api/runs/{run_id}/full returns 404 for unknown run."""
    resp = await client.get("/api/runs/nonexistent-run/full")
    assert resp.status_code == 404
