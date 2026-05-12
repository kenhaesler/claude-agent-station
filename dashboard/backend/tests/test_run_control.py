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


@pytest.mark.asyncio
async def test_trigger_run_inserts_pending_placeholder(client):
    """POST /api/runs/trigger must insert a Run(status='pending') BEFORE
    the launcher returns, so the dashboard shows feedback immediately
    (issue #346). The launcher call is mocked so the placeholder is the
    only side-effect we observe."""
    from unittest.mock import patch, AsyncMock
    from app.models import Run
    from sqlalchemy import select

    with patch("app.routers.runs.service_control.start_agent_service",
               new_callable=AsyncMock,
               return_value={"success": True, "detail": "accepted",
                             "status_code": 200}):
        resp = await client.post("/api/runs/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("run_id", "").startswith("run-")

    async with async_session() as db:
        rows = (await db.execute(
            select(Run).where(Run.run_id == body["run_id"])
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "pending"


# --- Zombie subprocess auto-recovery on 409 (#360 option 3) ----------------


@pytest.mark.asyncio
async def test_trigger_recovers_from_zombie_subprocess(setup_db):
    """When the launcher reports 409 and the dashboard's last_event_at is
    stale, start_agent_service force-stops and retries. #360 option 3."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from app.models import Run
    from app.services import service_control

    # Seed a stale run row (last_event_at > 60s ago)
    async with async_session() as db:
        db.add(Run(
            run_id="run-zombie-target",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            last_event_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ))
        await db.commit()

    # Mock the launcher conversation:
    #   1st /run → 409
    #   /status → running=True
    #   /stop → 200
    #   /status (poll) → running=False
    #   2nd /run → 200
    calls: list[tuple[str, str]] = []

    async def fake_launcher_call(method: str, path: str, json_body=None) -> dict:
        calls.append((method, path))
        status_call_count = sum(1 for m, p in calls if p == "/status")
        if path == "/run" and len(calls) == 1:
            return {"success": False, "status_code": 409,
                    "detail": "A run is already in progress (pid=42)"}
        if path == "/status" and status_call_count == 1:
            return {"success": True, "running": True, "pid": 42}
        if path == "/stop":
            return {"success": True, "detail": "stopped"}
        if path == "/status":
            return {"success": True, "running": False}
        if path == "/run":
            return {"success": True, "detail": "accepted", "status_code": 200}
        return {"success": False, "error": f"unexpected call: {method} {path}"}

    with patch("app.services.service_control._launcher_call",
               side_effect=fake_launcher_call):
        with patch.dict("os.environ", {"STATION_DEPLOY_MODE": "compose"}):
            result = await service_control.start_agent_service(
                hint_run_id="run-zombie-target-v2"
            )

    assert result.get("success") is True
    # Verify the recovery dance happened
    paths_called = [p for _, p in calls]
    assert paths_called.count("/run") == 2   # initial + retry
    assert "/stop" in paths_called           # zombie was killed


@pytest.mark.asyncio
async def test_trigger_propagates_409_when_run_is_actually_active(setup_db):
    """When the dashboard's last_event_at is FRESH, the 409 must
    propagate — the run is really running. #360 option 3."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from app.models import Run
    from app.services import service_control

    async with async_session() as db:
        db.add(Run(
            run_id="run-actually-active",
            status="running",
            started_at=datetime.now(timezone.utc),
            last_event_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    async def fake_launcher_call(method: str, path: str, json_body=None) -> dict:
        if path == "/run":
            return {"success": False, "status_code": 409,
                    "detail": "A run is already in progress"}
        if path == "/status":
            return {"success": True, "running": True, "pid": 42}
        return {"success": True}

    with patch("app.services.service_control._launcher_call",
               side_effect=fake_launcher_call):
        with patch.dict("os.environ", {"STATION_DEPLOY_MODE": "compose"}):
            result = await service_control.start_agent_service(hint_run_id="x")

    assert result.get("success") is False
    assert result.get("status_code") == 409
    assert "in progress" in result.get("error", "")


@pytest.mark.asyncio
async def test_recovery_targets_orchestrator_not_fresh_teammate(setup_db):
    """R3: In Agent Teams mode the launcher's _current is always the
    orchestrator (employee_index=0, started first). If a teammate row has
    a fresh heartbeat but the orchestrator row is stale, recovery must
    declare the orchestrator a zombie and force-stop — not be fooled by
    the fresh teammate. #360 R3."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from app.models import Run
    from app.services import service_control

    now = datetime.now(timezone.utc)

    async with async_session() as db:
        # Orchestrator run: stale heartbeat (> RUN_STALE_THRESHOLD_S ago)
        db.add(Run(
            run_id="run-orch-stale",
            status="running",
            employee_index=0,
            started_at=now - timedelta(minutes=10),
            last_event_at=now - timedelta(minutes=5),
        ))
        # Teammate run: fresh heartbeat (should NOT prevent recovery)
        db.add(Run(
            run_id="run-orch-stale-e1",
            status="running",
            employee_index=1,
            started_at=now - timedelta(minutes=9),
            last_event_at=now,  # just now
        ))
        await db.commit()

    calls: list[tuple[str, str]] = []

    async def fake_launcher_call(method: str, path: str, json_body=None) -> dict:
        calls.append((method, path))
        status_call_count = sum(1 for m, p in calls if p == "/status")
        if path == "/run" and len(calls) == 1:
            return {"success": False, "status_code": 409,
                    "detail": "A run is already in progress (pid=77)"}
        if path == "/status" and status_call_count == 1:
            return {"success": True, "running": True, "pid": 77}
        if path == "/stop":
            return {"success": True, "detail": "stopped"}
        if path == "/status":
            return {"success": True, "running": False}
        if path == "/run":
            return {"success": True, "detail": "accepted", "status_code": 200}
        return {"success": False, "error": f"unexpected call: {method} {path}"}

    with patch("app.services.service_control._launcher_call",
               side_effect=fake_launcher_call):
        with patch.dict("os.environ", {"STATION_DEPLOY_MODE": "compose"}):
            result = await service_control.start_agent_service(
                hint_run_id="run-new-run"
            )

    # Recovery must have fired (zombie reaped and trigger retried)
    assert result.get("success") is True, f"expected success, got: {result}"
    paths_called = [p for _, p in calls]
    assert paths_called.count("/run") == 2, "should have retried /run after stop"
    assert "/stop" in paths_called, "orchestrator zombie should have been stopped"
