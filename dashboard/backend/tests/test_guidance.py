"""Tests for POST /api/coordinator/guidance endpoint.

The legacy ``agent.coordinator.guidance`` module was removed when the
project moved to Claude Agent SDK Agent Teams mode. The endpoint now
acts as a shim that translates legacy guidance payloads into a
``RunControl`` row on the Mission Control control queue and emits an
event via ``app.services.event_bus.publish``.

Covers:
- 200 on a successful guidance message against a running run
- 200 with ``guidance_type='stop'`` mapping to a ``stop`` action
- 400 when ``content`` is empty or whitespace
- 404 when ``run_id`` does not exist
- 409 when the run is in a terminal state / has ``finished_at`` set
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.database import engine, Base, async_session
from app.models import Project, Run, RunControl


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


async def _seed_project(repo: str = "test/repo") -> int:
    async with async_session() as session:
        project = Project(
            repo=repo,
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project.id


async def _seed_run(
    run_id: str,
    status: str = "running",
    finished_at: datetime | None = None,
    project_id: int | None = None,
) -> None:
    """Insert a Run row with the given status."""
    async with async_session() as session:
        run = Run(
            run_id=run_id,
            project_id=project_id,
            status=status,
            employee_index=0,
            started_at=datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc),
            finished_at=finished_at,
        )
        session.add(run)
        await session.commit()


def _payload(run_id: str, **overrides) -> dict:
    """Build a guidance request payload."""
    payload = {
        "run_id": run_id,
        "employee_index": 0,
        "guidance_type": "info",
        "content": "Focus on tests",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_guidance_success_message(client):
    """A valid guidance message against a running run returns 200,
    inserts a RunControl row with action='message', and publishes."""
    await _seed_project()
    await _seed_run("run-success")

    with patch(
        "app.services.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish:
        resp = await client.post(
            "/api/coordinator/guidance",
            json=_payload("run-success"),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["action"] == "message"
    assert isinstance(body["control_id"], int)

    # RunControl row was written with the prefixed message
    async with async_session() as session:
        rows = (await session.execute(
            select(RunControl).where(RunControl.run_id == "run-success")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "message"
    assert rows[0].payload is not None
    assert "[operator-info]" in rows[0].payload
    assert "Focus on tests" in rows[0].payload
    assert rows[0].requested_by == "guidance"

    # An event was published
    mock_publish.assert_awaited_once()
    event = mock_publish.await_args.args[0]
    assert event["type"] == "run_control_message"
    assert event["data"]["run_id"] == "run-success"


@pytest.mark.asyncio
async def test_guidance_stop_maps_to_stop_action(client):
    """guidance_type='stop' is mapped to action='stop' with a NULL payload."""
    await _seed_project()
    await _seed_run("run-stop")

    with patch(
        "app.services.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish:
        resp = await client.post(
            "/api/coordinator/guidance",
            json=_payload("run-stop", guidance_type="stop", content="halt now"),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "stop"

    async with async_session() as session:
        row = (await session.execute(
            select(RunControl).where(RunControl.run_id == "run-stop")
        )).scalar_one()
    assert row.action == "stop"
    assert row.payload is None

    mock_publish.assert_awaited_once()
    assert mock_publish.await_args.args[0]["type"] == "run_control_stop"


@pytest.mark.asyncio
async def test_guidance_empty_content_400(client):
    """Whitespace-only content is rejected with 400."""
    await _seed_project()
    await _seed_run("run-empty")

    resp = await client.post(
        "/api/coordinator/guidance",
        json=_payload("run-empty", content="   "),
    )

    assert resp.status_code == 400
    assert "content" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guidance_unknown_run_404(client):
    """Guidance against a non-existent run_id returns 404."""
    await _seed_project()  # no Run row inserted

    resp = await client.post(
        "/api/coordinator/guidance",
        json=_payload("run-nonexistent"),
    )

    assert resp.status_code == 404
    assert "run not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "interrupted"])
async def test_guidance_terminal_run_409(client, terminal_status: str):
    """Guidance against a terminal run returns 409 — the orchestrator is
    no longer draining its control queue."""
    await _seed_project()
    await _seed_run(
        f"run-{terminal_status}",
        status=terminal_status,
        finished_at=datetime(2026, 3, 14, 13, 0, 0, tzinfo=timezone.utc),
    )

    resp = await client.post(
        "/api/coordinator/guidance",
        json=_payload(f"run-{terminal_status}"),
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "no longer active" in detail
    assert terminal_status in detail


@pytest.mark.asyncio
async def test_guidance_finished_at_set_409(client):
    """A run with finished_at set is treated as terminal even if its
    status string is something else (defence in depth)."""
    await _seed_project()
    await _seed_run(
        "run-finished",
        status="running",  # status string disagrees with finished_at
        finished_at=datetime(2026, 3, 14, 13, 0, 0, tzinfo=timezone.utc),
    )

    resp = await client.post(
        "/api/coordinator/guidance",
        json=_payload("run-finished"),
    )

    assert resp.status_code == 409
