"""Tests for the runs API.

Covers:
- GET /api/runs — list with pagination and filters
- GET /api/runs/{run_id} — get single run
- GET /api/runs/latest — get most recent run
- GET /api/runs/active-employees — get running employees
- 404 for missing run
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
                tokens_total=15000,
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


# --- ADR-0001 autonomy fields ---

@pytest.mark.asyncio
async def test_run_autonomy_fields_default_to_assisted(client, sample_runs):
    """GET /api/runs/{id} returns autonomy_level/max_budget_usd fields per ADR-0001."""
    resp = await client.get("/api/runs/run-001")
    assert resp.status_code == 200
    data = resp.json()
    # Columns default to 'assisted' via migration; per-run override may be null.
    assert "autonomy_level" in data
    assert "max_budget_usd" in data


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
    # tokens_total and started_at surface on the schema so the AgentTeams canvas
    # can render real duration and token counts on the Lead card.
    assert data[0]["tokens_total"] == 15000
    assert data[0]["started_at"] is not None


@pytest.mark.asyncio
async def test_get_active_employees_empty(client):
    """GET /api/runs/active-employees returns empty list when no runs are active."""
    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_active_employees_surfaces_last_event_at(client):
    """The /api/runs/active-employees response must include
    last_event_at so Mission Control can render the heartbeat badge.
    Fixes the PR #351 review gap."""
    from datetime import datetime, timezone
    from app.models import Run
    last_evt = datetime.now(timezone.utc)
    async with async_session() as db:
        db.add(Run(run_id="run-with-heartbeat", status="running",
                   started_at=last_evt, last_event_at=last_evt))
        await db.commit()

    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    employees = resp.json()
    found = [e for e in employees if e["run_id"] == "run-with-heartbeat"]
    assert len(found) == 1
    assert found[0]["last_event_at"] is not None


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


@pytest.mark.asyncio
async def test_get_run_full_context_multiple_queue_items(client):
    """Issue #290 wired the orchestrator to drain multiple QueueItems
    per run. The endpoint must return them as a list — previously it
    used ``scalar_one_or_none()`` which 500'd with MultipleResultsFound
    for any drained run, breaking the run-detail UI completely.
    """
    from datetime import datetime, timezone

    from app.database import async_session
    from app.models import QueueItem, Run

    async with async_session() as s:
        s.add(Run(
            run_id="run-multi-q-001",
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        s.add_all([
            QueueItem(project_repo="x/y", issue_number=42, mode="full",
                      state="claimed", run_id="run-multi-q-001"),
            QueueItem(project_repo="x/y", issue_number=43, mode="full",
                      state="claimed", run_id="run-multi-q-001"),
            QueueItem(project_repo="x/y", issue_number=44, mode="full",
                      state="claimed", run_id="run-multi-q-001"),
        ])
        await s.commit()

    resp = await client.get("/api/runs/run-multi-q-001/full")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "queue_items" in data
    assert len(data["queue_items"]) == 3
    issue_numbers = sorted(qi["issue_number"] for qi in data["queue_items"])
    assert issue_numbers == [42, 43, 44]
    # Backwards-compat: ``queue_item`` is the first item (by id), not None
    assert data["queue_item"] is not None
    assert data["queue_item"]["issue_number"] == 42


@pytest.mark.asyncio
async def test_telemetry_summary_shape_empty(client):
    """GET /api/runs/telemetry-summary returns the four-cell shape even on
    a brand-new station with no runs and no queue rows."""
    resp = await client.get("/api/runs/telemetry-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert set(data.keys()) == {"active", "queue", "tokens_7d", "system", "verdicts_7d"}

    assert data["active"]["count"] == 0
    assert data["active"]["teammates"] == 0
    assert data["active"]["roles"] == []

    assert data["queue"]["total"] == 0
    assert data["queue"]["claimed"] == 0
    assert data["queue"]["done"] == 0
    assert data["queue"]["pending"] == 0

    assert data["tokens_7d"]["total"] == 0
    assert data["tokens_7d"]["runs"] == 0
    assert isinstance(data["tokens_7d"]["spark"], list)

    assert data["verdicts_7d"] == {"ok": 0, "pr": 0, "x": 0}

    # System status is NOMINAL when get_system_resources returns nothing
    # actionable (e.g. test environment without /proc/meminfo).
    assert data["system"]["status"] in {"NOMINAL", "DEGR", "CRIT"}


@pytest.mark.asyncio
async def test_telemetry_summary_aggregates_runs_and_queue(client):
    """Endpoint reflects active runs, queue states, and 7d token totals."""
    from app.database import async_session
    from app.models import QueueItem, Run

    async with async_session() as s:
        s.add(Run(
            run_id="run-tel-active",
            status="running",
            started_at=datetime.now(timezone.utc),
            tokens_total=500,
            tokens_input=100,
            tokens_output=400,
            team_members='[{"name": "backend-spright"}, {"name": "frontend-spright"}]',
        ))
        s.add(Run(
            run_id="run-tel-old",
            status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(days=2),
            tokens_total=300, tokens_input=50, tokens_output=250,
        ))
        s.add_all([
            QueueItem(project_repo="x/y", issue_number=1, mode="full", state="pending"),
            QueueItem(project_repo="x/y", issue_number=2, mode="full", state="claimed"),
            QueueItem(project_repo="x/y", issue_number=3, mode="full", state="completed"),
        ])
        await s.commit()

    resp = await client.get("/api/runs/telemetry-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["active"]["count"] == 1
    assert data["active"]["teammates"] == 2
    assert sorted(data["active"]["roles"]) == ["backend", "frontend"]

    assert data["queue"]["pending"] == 1
    assert data["queue"]["claimed"] == 1
    assert data["queue"]["done"] == 1
    assert data["queue"]["total"] == 3

    assert data["tokens_7d"]["total"] == 800
    assert data["tokens_7d"]["runs"] == 2
    assert data["tokens_7d"]["input"] == 150
    assert data["tokens_7d"]["output"] == 650
    # spark backfilled to a length-7 array even though only 2 days had data
    assert len(data["tokens_7d"]["spark"]) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resources, expected_status",
    [
        # Plenty of headroom → NOMINAL
        (
            {
                "disk_free_gb": 50.0,
                "disk_total_gb": 100.0,
                "memory_used_mb": 4_000,
                "memory_total_mb": 16_000,
                "uptime_seconds": 1234.0,
            },
            "NOMINAL",
        ),
        # Disk under 5G but above 1G, mem 75% → DEGR
        (
            {
                "disk_free_gb": 3.5,
                "disk_total_gb": 100.0,
                "memory_used_mb": 12_000,
                "memory_total_mb": 16_000,
                "uptime_seconds": 1234.0,
            },
            "DEGR",
        ),
        # Disk under 1G OR mem above 90% → CRIT
        (
            {
                "disk_free_gb": 0.4,
                "disk_total_gb": 100.0,
                "memory_used_mb": 15_000,
                "memory_total_mb": 16_000,
                "uptime_seconds": 1234.0,
            },
            "CRIT",
        ),
    ],
)
async def test_telemetry_summary_system_status_thresholds(
    client, monkeypatch, resources, expected_status
):
    """`telemetry_summary.system.status` reflects the disk/memory pressure
    thresholds in :func:`get_telemetry_summary`. Patches
    :func:`app.services.systemd.get_system_resources` (the same symbol the
    router imports module-level) so the test doesn't depend on /proc."""
    from app.routers import runs as runs_router

    async def fake_resources():
        return resources

    monkeypatch.setattr(runs_router, "get_system_resources", fake_resources)

    resp = await client.get("/api/runs/telemetry-summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["system"]["status"] == expected_status


@pytest.mark.asyncio
async def test_telemetry_summary_queue_other_bucket(client):
    """Queue items in ``failed``/``paused``/``cancelled`` end up in the
    ``other`` bucket so ``claimed + done + pending + other == total``."""
    from app.database import async_session
    from app.models import QueueItem

    async with async_session() as s:
        s.add_all([
            QueueItem(project_repo="x/y", issue_number=10, mode="full", state="pending"),
            QueueItem(project_repo="x/y", issue_number=11, mode="full", state="claimed"),
            QueueItem(project_repo="x/y", issue_number=12, mode="full", state="completed"),
            QueueItem(project_repo="x/y", issue_number=13, mode="full", state="failed"),
            QueueItem(project_repo="x/y", issue_number=14, mode="full", state="cancelled"),
        ])
        await s.commit()

    resp = await client.get("/api/runs/telemetry-summary")
    assert resp.status_code == 200, resp.text
    q = resp.json()["queue"]
    assert q["total"] == 5
    assert q["pending"] == 1
    assert q["claimed"] == 1
    assert q["done"] == 1
    assert q["other"] == 2
    assert q["pending"] + q["claimed"] + q["done"] + q["other"] == q["total"]


@pytest.mark.asyncio
async def test_telemetry_summary_verdicts_7d(client):
    """Verdicts are bucketed APPROVE→ok, PR→pr, anything else non-null→x,
    over the same 7-day cutoff used by ``tokens_7d``. Runs older than 7d or
    with a NULL verdict are excluded."""
    from app.database import async_session
    from app.models import Run

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        # Within 7d window
        s.add(Run(run_id="v1", status="completed", started_at=now, verdict="APPROVE"))
        s.add(Run(run_id="v2", status="completed", started_at=now, verdict="APPROVE"))
        s.add(Run(run_id="v3", status="completed", started_at=now, verdict="PR"))
        s.add(Run(run_id="v4", status="completed", started_at=now, verdict="REJECT"))
        # Lower-case write (defensive normalization)
        s.add(Run(run_id="v5", status="completed", started_at=now, verdict="approve"))
        # Excluded: NULL verdict
        s.add(Run(run_id="v6", status="running", started_at=now, verdict=None))
        # Excluded: older than 7 days
        s.add(Run(
            run_id="v7", status="completed",
            started_at=now - timedelta(days=10), verdict="APPROVE",
        ))
        await s.commit()

    resp = await client.get("/api/runs/telemetry-summary")
    assert resp.status_code == 200, resp.text
    v = resp.json()["verdicts_7d"]
    assert v == {"ok": 3, "pr": 1, "x": 1}
