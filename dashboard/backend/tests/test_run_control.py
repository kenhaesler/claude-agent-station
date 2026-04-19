"""Tests for Mission Control run intervention endpoints (Phase A)."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import Run, RunControl, StationControl


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        session.add(StationControl(id=1, global_pause=False))
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def live_run(setup_db):
    async with async_session() as session:
        run = Run(
            run_id="run-mc-001",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
    return "run-mc-001"


async def _pending_controls(run_id: str) -> list[RunControl]:
    async with async_session() as session:
        result = await session.execute(
            select(RunControl).where(RunControl.run_id == run_id)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_pause_unknown_run_returns_404(client):
    resp = await client.post("/api/runs/does-not-exist/pause")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_enqueues_control_row(client, live_run):
    resp = await client.post(f"/api/runs/{live_run}/pause")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == live_run
    assert body["action"] == "pause"
    assert body["control_id"] > 0

    rows = await _pending_controls(live_run)
    assert len(rows) == 1
    assert rows[0].action == "pause"
    assert rows[0].consumed_at is None


@pytest.mark.asyncio
async def test_resume_enqueues_control_row(client, live_run):
    await client.post(f"/api/runs/{live_run}/pause")
    resp = await client.post(f"/api/runs/{live_run}/resume")
    assert resp.status_code == 200
    rows = await _pending_controls(live_run)
    assert [r.action for r in rows] == ["pause", "resume"]


@pytest.mark.asyncio
async def test_stop_enqueues_control_row(client, live_run):
    resp = await client.post(f"/api/runs/{live_run}/stop")
    assert resp.status_code == 200
    assert resp.json()["action"] == "stop"


@pytest.mark.asyncio
async def test_message_requires_text(client, live_run):
    resp = await client.post(
        f"/api/runs/{live_run}/message",
        json={"text": "   "},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_message_stores_payload(client, live_run):
    import json as _json

    resp = await client.post(
        f"/api/runs/{live_run}/message",
        json={"text": "focus on the failing test"},
    )
    assert resp.status_code == 200
    rows = await _pending_controls(live_run)
    assert len(rows) == 1
    assert rows[0].action == "message"
    payload = _json.loads(rows[0].payload)
    assert payload["text"] == "focus on the failing test"


@pytest.mark.asyncio
async def test_message_rejects_oversize_text(client, live_run):
    resp = await client.post(
        f"/api/runs/{live_run}/message",
        json={"text": "x" * 5000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_global_pause_flag_round_trip(client):
    initial = await client.get("/api/system/pause")
    assert initial.status_code == 200
    assert initial.json()["global_pause"] is False

    set_resp = await client.post("/api/system/pause")
    assert set_resp.status_code == 200
    assert set_resp.json()["global_pause"] is True

    # Confirm via GET
    check = await client.get("/api/system/pause")
    assert check.json()["global_pause"] is True

    clear_resp = await client.post("/api/system/resume")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["global_pause"] is False


@pytest.mark.asyncio
async def test_coordinator_guidance_shim_forwards_to_message_queue(client, live_run):
    """The legacy /api/coordinator/guidance endpoint should now enqueue a
    Mission Control control row instead of returning 501."""
    resp = await client.post(
        "/api/coordinator/guidance",
        json={
            "run_id": live_run,
            "employee_index": 0,
            "guidance_type": "info",
            "content": "try the alternative approach",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "message"
    rows = await _pending_controls(live_run)
    assert rows and rows[0].action == "message"


@pytest.mark.asyncio
async def test_coordinator_guidance_stop_enqueues_stop(client, live_run):
    resp = await client.post(
        "/api/coordinator/guidance",
        json={
            "run_id": live_run,
            "employee_index": 0,
            "guidance_type": "stop",
            "content": "halt the work",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "stop"
    rows = await _pending_controls(live_run)
    assert rows and rows[0].action == "stop"


# --- Orphan-run rejection (hotfix for Mission Control Phase A) -------------
# Before the hotfix the UI would silently route operator messages to a run
# whose orchestrator had already exited. The backend accepted them, wrote
# a row to run_controls, and emitted SSE — but nobody ever drained the queue
# so the agent never saw the message. These tests pin the fix: every
# intervention endpoint must return 409 once the run has terminated.


@pytest_asyncio.fixture
async def dead_run(setup_db):
    """A run that has already terminated (status=completed, finished_at set).

    Mirrors the exact condition that let run-20260419T145408Z swallow the
    user's "What are you working on?" message.
    """
    async with async_session() as session:
        run = Run(
            run_id="run-mc-dead",
            status="completed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
    return "run-mc-dead"


@pytest.mark.asyncio
async def test_message_on_terminated_run_returns_409(client, dead_run):
    resp = await client.post(
        f"/api/runs/{dead_run}/message",
        json={"text": "still there?"},
    )
    assert resp.status_code == 409
    assert "no longer active" in resp.json()["detail"]
    # And crucially: no row was written to run_controls.
    assert await _pending_controls(dead_run) == []


@pytest.mark.asyncio
async def test_pause_on_terminated_run_returns_409(client, dead_run):
    resp = await client.post(f"/api/runs/{dead_run}/pause")
    assert resp.status_code == 409
    assert await _pending_controls(dead_run) == []


@pytest.mark.asyncio
async def test_stop_on_terminated_run_returns_409(client, dead_run):
    resp = await client.post(f"/api/runs/{dead_run}/stop")
    assert resp.status_code == 409
    assert await _pending_controls(dead_run) == []


@pytest.mark.asyncio
async def test_resume_on_terminated_run_returns_409(client, dead_run):
    resp = await client.post(f"/api/runs/{dead_run}/resume")
    assert resp.status_code == 409
    assert await _pending_controls(dead_run) == []


@pytest.mark.asyncio
async def test_coordinator_guidance_on_terminated_run_returns_409(client, dead_run):
    resp = await client.post(
        "/api/coordinator/guidance",
        json={
            "run_id": dead_run,
            "employee_index": 0,
            "guidance_type": "info",
            "content": "anything",
        },
    )
    assert resp.status_code == 409
    assert await _pending_controls(dead_run) == []


# --- Orphan sweep on terminal transition -----------------------------------
# When a run finishes, any pending run_controls rows must be marked expired
# and an SSE event fired so the UI can flip the pending badge to red.


@pytest.mark.asyncio
async def test_finished_webhook_expires_orphan_controls(client, live_run):
    """A message queued mid-run should be marked expired as soon as the
    orchestrator reports the run finished."""
    # 1. Queue a message while the run is still live.
    msg_resp = await client.post(
        f"/api/runs/{live_run}/message",
        json={"text": "hello agent"},
    )
    assert msg_resp.status_code == 200
    rows_before = await _pending_controls(live_run)
    assert len(rows_before) == 1
    assert rows_before[0].consumed_at is None

    # 2. Simulate run finish via the webhook handler.
    from app.models import Run as RunModel
    from app.schemas import WebhookRunEvent
    from app.services.run_lifecycle import handle_finished, SWEEPER_EXPIRED

    async with async_session() as session:
        run = (
            await session.execute(select(RunModel).where(RunModel.run_id == live_run))
        ).scalar_one()
        event = WebhookRunEvent(
            event="finished",
            run_id=live_run,
            status="completed",
        )
        await handle_finished(session, event, None, run)
        await session.commit()

    # 3. The row should now be consumed with the sweeper sentinel.
    rows_after = await _pending_controls(live_run)
    assert len(rows_after) == 1
    assert rows_after[0].consumed_at is not None
    assert rows_after[0].requested_by == SWEEPER_EXPIRED


@pytest.mark.asyncio
async def test_expire_orphan_controls_is_idempotent(client, live_run):
    """Replaying a finished event should not double-expire rows."""
    from app.models import Run as RunModel
    from app.services.run_lifecycle import expire_orphan_controls

    await client.post(
        f"/api/runs/{live_run}/message",
        json={"text": "first"},
    )

    async with async_session() as session:
        first = await expire_orphan_controls(session, live_run)
        await session.commit()
        second = await expire_orphan_controls(session, live_run)
        await session.commit()

    assert len(first) == 1
    assert second == []  # nothing left to expire
