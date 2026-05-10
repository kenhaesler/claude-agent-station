"""Tests for coordinator API endpoints.

Covers:
- GET /api/coordinator/tasks - list tasks
- GET /api/coordinator/tasks/{task_id} - get single task
- GET /api/coordinator/tasks/{task_id}/details - get task with employee report and log excerpt
- GET /api/coordinator/dag/{run_id} - get full DAG
- GET /api/coordinator/messages - list messages
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import CoordinatorMessage, CoordinatorTask, Project, Run


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
async def sample_data(setup_db):
    """Insert sample coordinator tasks and a run for testing."""
    async with async_session() as session:
        # Create a project
        project = Project(
            repo="test/repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.flush()

        # Create a run with employee report
        run = Run(
            run_id="run-20260312T120000Z",
            project_id=project.id,
            status="success",
            verdict="APPROVE",
            employee_index=0,
            employee_report=json.dumps({
                "status": "success",
                "issue_number": 42,
                "issue_title": "Fix login button",
                "branch": "autonomous/issue-42",
                "requirements": [
                    {"description": "Fix button color", "source": "issue body", "completed": True},
                ],
                "files_changed": ["src/login.tsx"],
                "tests_run": True,
                "tests_passed": True,
                "test_output_summary": "5 tests passed",
                "notes": "All good",
            }),
        )
        session.add(run)

        # Create coordinator tasks
        task1 = CoordinatorTask(
            id="task-run-20260312T120000Z-0",
            run_id="run-20260312T120000Z",
            project_repo="test/repo",
            title="Fix login form",
            description="Fix the login form validation",
            status="completed",
            employee_index=0,
            depends_on=None,
            exit_code=0,
            result_summary="Fixed login validation, 5 tests passing",
            branch="autonomous/issue-42",
            created_at=datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 12, 12, 5, 0, tzinfo=timezone.utc),
            touched_files=json.dumps(["src/login.tsx", "src/login.test.tsx"]),
        )
        task2 = CoordinatorTask(
            id="task-run-20260312T120000Z-1",
            run_id="run-20260312T120000Z",
            project_repo="test/repo",
            title="Update API docs",
            description="Update the API documentation",
            status="running",
            employee_index=1,
            depends_on=json.dumps(["task-run-20260312T120000Z-0"]),
            created_at=datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 3, 12, 12, 5, 0, tzinfo=timezone.utc),
        )
        session.add_all([task1, task2])

        # Create a coordinator message
        msg = CoordinatorMessage(
            run_id="run-20260312T120000Z",
            task_id="task-run-20260312T120000Z-0",
            direction="to_employee",
            message_type="guidance",
            content=json.dumps({"type": "info", "content": "Focus on tests"}),
            employee_index=0,
        )
        session.add(msg)
        await session.commit()


@pytest.mark.asyncio
async def test_list_tasks(client, sample_data):
    """GET /api/coordinator/tasks returns all tasks."""
    resp = await client.get("/api/coordinator/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    titles = {t["title"] for t in data}
    assert "Fix login form" in titles
    assert "Update API docs" in titles


@pytest.mark.asyncio
async def test_list_tasks_filter_by_run_id(client, sample_data):
    """GET /api/coordinator/tasks?run_id=... filters correctly."""
    resp = await client.get("/api/coordinator/tasks?run_id=run-20260312T120000Z")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get("/api/coordinator/tasks?run_id=nonexistent")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(client, sample_data):
    """GET /api/coordinator/tasks?status=completed filters correctly."""
    resp = await client.get("/api/coordinator/tasks?status=completed")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Fix login form"


@pytest.mark.asyncio
async def test_get_single_task(client, sample_data):
    """GET /api/coordinator/tasks/{task_id} returns the task."""
    resp = await client.get("/api/coordinator/tasks/task-run-20260312T120000Z-0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Fix login form"
    assert data["status"] == "completed"
    assert data["result_summary"] == "Fixed login validation, 5 tests passing"
    assert data["branch"] == "autonomous/issue-42"


@pytest.mark.asyncio
async def test_get_single_task_not_found(client, sample_data):
    """GET /api/coordinator/tasks/{task_id} returns 404 for unknown task."""
    resp = await client.get("/api/coordinator/tasks/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_task_details(client, sample_data):
    """GET /api/coordinator/tasks/{task_id}/details returns enriched data."""
    resp = await client.get("/api/coordinator/tasks/task-run-20260312T120000Z-0/details")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Fix login form"
    assert data["result_summary"] == "Fixed login validation, 5 tests passing"
    # Should include employee report from run
    assert data["employee_report"] is not None
    assert data["employee_report"]["status"] == "success"
    assert data["employee_report"]["issue_title"] == "Fix login button"
    assert len(data["employee_report"]["requirements"]) == 1


@pytest.mark.asyncio
async def test_get_task_details_not_found(client, sample_data):
    """GET /api/coordinator/tasks/{task_id}/details returns 404 for unknown task."""
    resp = await client.get("/api/coordinator/tasks/nonexistent/details")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_task_details_with_log_file(client, sample_data):
    """GET /api/coordinator/tasks/{task_id}/details includes log excerpt when log file exists."""
    # Create a temp log file and assign it to the task
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        for i in range(150):
            f.write(f"Log line {i}\n")
        log_path = f.name

    try:
        # Update task with log_path
        async with async_session() as session:
            from sqlalchemy import update
            await session.execute(
                update(CoordinatorTask)
                .where(CoordinatorTask.id == "task-run-20260312T120000Z-0")
                .values(log_path=log_path)
            )
            await session.commit()

        resp = await client.get("/api/coordinator/tasks/task-run-20260312T120000Z-0/details")
        assert resp.status_code == 200
        data = resp.json()
        assert data["log_excerpt"] is not None
        # Should contain the last 100 lines (lines 50-149)
        assert "Log line 149" in data["log_excerpt"]
        assert "Log line 50" in data["log_excerpt"]
        # Should NOT contain the first line (only last 100)
        assert "Log line 0\n" not in data["log_excerpt"]
    finally:
        os.unlink(log_path)


@pytest.mark.asyncio
async def test_get_dag(client, sample_data):
    """GET /api/coordinator/dag/{run_id} returns full DAG."""
    resp = await client.get("/api/coordinator/dag/run-20260312T120000Z")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-20260312T120000Z"
    assert data["project_repo"] == "test/repo"
    assert len(data["tasks"]) == 2
    assert "completed" in data["summary"]
    assert data["summary"]["completed"] == 1
    assert data["summary"]["running"] == 1


@pytest.mark.asyncio
async def test_get_dag_not_found(client, sample_data):
    """GET /api/coordinator/dag/{run_id} returns 404 for unknown run."""
    resp = await client.get("/api/coordinator/dag/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_messages(client, sample_data):
    """GET /api/coordinator/messages returns messages."""
    resp = await client.get("/api/coordinator/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "to_employee"
    assert data[0]["message_type"] == "guidance"


@pytest.mark.asyncio
async def test_list_messages_filter_by_run_id(client, sample_data):
    """GET /api/coordinator/messages?run_id=... filters correctly."""
    resp = await client.get("/api/coordinator/messages?run_id=run-20260312T120000Z")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get("/api/coordinator/messages?run_id=nonexistent")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_task_new_fields_in_response(client, sample_data):
    """Verify new fields (result_summary, log_path, branch) are in task responses."""
    resp = await client.get("/api/coordinator/tasks/task-run-20260312T120000Z-0")
    assert resp.status_code == 200
    data = resp.json()
    # New fields should be present
    assert "result_summary" in data
    assert "log_path" in data
    assert "branch" in data
    assert data["branch"] == "autonomous/issue-42"


@pytest.mark.asyncio
async def test_task_depends_on_json(client, sample_data):
    """Verify depends_on field contains valid JSON."""
    resp = await client.get("/api/coordinator/tasks/task-run-20260312T120000Z-1")
    assert resp.status_code == 200
    data = resp.json()
    deps = json.loads(data["depends_on"])
    assert deps == ["task-run-20260312T120000Z-0"]


# Issue #336: per-teammate progress on the Fleet page
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teammate_progress_writes_per_task_counters(setup_db):
    """``handle_teammate_progress`` writes tokens/turns to dedicated columns
    and leaves ``touched_files`` alone (issue #336).

    Earlier code overloaded ``touched_files`` with a {tokens,turns} dict,
    which the API never read back and which clobbered the file-array
    contract. Verify the fix in both directions.
    """
    from app.schemas import WebhookRunEvent
    from app.services import coordinator_service

    async with async_session() as session:
        session.add(CoordinatorTask(
            id="task-336-progress",
            run_id="run-336",
            project_repo="test/repo",
            title="Build login form",
            status="running",
            employee_index=2,
        ))
        await session.commit()

    # First progress event lands.
    async with async_session() as session:
        await coordinator_service.handle_teammate_progress(
            session,
            WebhookRunEvent(
                event="teammate_progress",
                run_id="run-336",
                task_id="task-336-progress",
                tokens_total=4321,
                turns=7,
            ),
        )
        await session.commit()

    async with async_session() as session:
        ct = await session.get(CoordinatorTask, "task-336-progress")
        assert ct.tokens_total == 4321
        assert ct.turns == 7
        # touched_files must NOT be polluted with a tokens/turns dict.
        assert ct.touched_files is None

    # A second event arrives with a fresher snapshot — counters move forward.
    async with async_session() as session:
        await coordinator_service.handle_teammate_progress(
            session,
            WebhookRunEvent(
                event="teammate_progress",
                run_id="run-336",
                task_id="task-336-progress",
                tokens_total=9999,
                turns=15,
            ),
        )
        await session.commit()

    # A late, stale event must NOT roll counters backwards.
    async with async_session() as session:
        await coordinator_service.handle_teammate_progress(
            session,
            WebhookRunEvent(
                event="teammate_progress",
                run_id="run-336",
                task_id="task-336-progress",
                tokens_total=100,
                turns=1,
            ),
        )
        await session.commit()

    async with async_session() as session:
        ct = await session.get(CoordinatorTask, "task-336-progress")
        assert ct.tokens_total == 9999
        assert ct.turns == 15


@pytest.mark.asyncio
async def test_active_employees_surfaces_per_task_counters(client, setup_db):
    """``GET /api/runs/active-employees`` returns per-teammate tokens/turns
    from CoordinatorTask, not the lead's aggregate (issue #336).

    Reproduces the original Fleet bug: with two running teammates and a
    single parent Run, the endpoint must return distinct per-teammate
    counters rather than copying ``Run.tokens_total`` onto every row.
    """
    async with async_session() as session:
        session.add(Project(repo="test/repo", priority="medium", mode="full",
                            enabled=True, branch="main"))
        await session.flush()
        session.add(Run(
            run_id="run-336-fleet",
            project_id=None,
            status="running",
            tokens_total=12345,  # lead's aggregate — must not leak onto teammates
            turns=99,
            employee_index=0,
        ))
        session.add_all([
            CoordinatorTask(
                id="task-336-be",
                run_id="run-336-fleet",
                project_repo="test/repo",
                title="backend work",
                status="running",
                employee_index=1,
                claimed_by="backend-spright",
                tokens_total=2200,
                turns=4,
            ),
            CoordinatorTask(
                id="task-336-fe",
                run_id="run-336-fleet",
                project_repo="test/repo",
                title="frontend work",
                status="running",
                employee_index=2,
                claimed_by="frontend-spright",
                tokens_total=3300,
                turns=6,
            ),
        ])
        await session.commit()

    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    rows = resp.json()
    by_idx = {r["employee_index"]: r for r in rows}

    # Per-teammate rows expose their own counters, not the parent run's.
    assert by_idx[1]["tokens_total"] == 2200
    assert by_idx[1]["turns"] == 4
    assert by_idx[2]["tokens_total"] == 3300
    assert by_idx[2]["turns"] == 6


@pytest.mark.asyncio
async def test_task_completed_persists_final_counters(setup_db):
    """When a teammate finishes, ``handle_task_event(task_completed)``
    writes the final tokens/turns snapshot onto the CoordinatorTask
    (issue #336)."""
    from app.schemas import WebhookRunEvent
    from app.services import coordinator_service

    async with async_session() as session:
        session.add(CoordinatorTask(
            id="task-336-done",
            run_id="run-336",
            project_repo="test/repo",
            title="qa pass",
            status="running",
            tokens_total=1000,
            turns=2,
        ))
        await session.commit()

    async with async_session() as session:
        await coordinator_service.handle_task_event(
            session,
            WebhookRunEvent(
                event="teammate_completed",
                run_id="run-336",
                task_id="task-336-done",
                status="success",
                tokens_total=5500,
                turns=12,
            ),
            "task_completed",
        )
        await session.commit()

    async with async_session() as session:
        ct = await session.get(CoordinatorTask, "task-336-done")
        assert ct.status == "completed"
        assert ct.tokens_total == 5500
        assert ct.turns == 12
