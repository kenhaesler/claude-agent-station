"""Tests for POST /api/runs/trigger.

The endpoint has two paths depending on ``STATION_DEPLOY_MODE``:

- ``compose`` → POST to the agent container's launcher at
  ``STATION_AGENT_LAUNCHER_URL`` (the base URL, e.g. ``http://agent:8421``).
  Token forwarded as ``X-Launcher-Token`` when set.
- ``systemd`` (default) → ``systemctl start claude-agent.service``
  (bare-metal systemd deployment).

Both paths are exercised here so the compose changes can't silently break
the systemd path or vice versa. We use ``respx`` to mock httpx at the
transport layer — patching ``httpx.AsyncClient.post`` would also intercept
the test client, since both use the same class.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app

# The launcher base URL (env var) is ``http://agent:8421``; respx still
# mocks the full ``/run`` URL because that's what the launcher exposes.
LAUNCHER_URL = "http://agent:8421/run"


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_trigger_uses_launcher_when_env_set(client, monkeypatch):
    """When deploy mode is compose, the dashboard should POST to the
    launcher and propagate the launcher's response body."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)

    launcher_body = {"status": "triggered", "pid": 1234, "log": "/var/log/claude-agent/launcher.out"}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(LAUNCHER_URL).respond(200, json=launcher_body)
        resp = await client.post("/api/runs/trigger")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triggered"
    assert body["pid"] == 1234
    # No token header sent when env unset.
    assert "X-Launcher-Token" not in route.calls[0].request.headers


@pytest.mark.asyncio
async def test_trigger_forwards_launcher_token_header(client, monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-abc")

    with respx.mock() as mock:
        route = mock.post(LAUNCHER_URL).respond(200, json={"status": "triggered", "pid": 1})
        await client.post("/api/runs/trigger")

    assert route.calls[0].request.headers["X-Launcher-Token"] == "secret-abc"


@pytest.mark.asyncio
async def test_trigger_propagates_launcher_4xx(client, monkeypatch):
    """A 409 from the launcher (run already in progress) should reach the
    operator with the same status code so the UI can show a clean message.

    Since PR #364, a 409 also kicks off zombie-recovery: the dashboard
    checks /status and the most-recent active Run's last_event_at. When
    the heartbeat is fresh (<RUN_STALE_THRESHOLD_S), recovery declines
    and the 409 propagates. We seed a fresh-heartbeat Run row so the
    recovery path short-circuits and the test's stated invariant is
    preserved."""
    from datetime import datetime, timezone
    from app.models import Run
    from app.database import async_session

    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")

    # Seed a fresh-heartbeat orchestrator Run row so recovery declines.
    async with async_session() as db:
        db.add(Run(
            run_id="run-already-running",
            status="running",
            employee_index=0,
            started_at=datetime.now(timezone.utc),
            last_event_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    with respx.mock() as mock:
        mock.post(LAUNCHER_URL).respond(409, text="A run is already in progress")
        mock.get("http://agent:8421/status").respond(
            200, json={"running": True, "pid": 42}
        )
        resp = await client.post("/api/runs/trigger")

    assert resp.status_code == 409
    assert "in progress" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_trigger_returns_502_when_launcher_unreachable(client, monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")

    with respx.mock() as mock:
        mock.post(LAUNCHER_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        resp = await client.post("/api/runs/trigger")

    assert resp.status_code == 502
    assert "launcher unreachable" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_trigger_falls_back_to_systemctl_when_env_unset(client, monkeypatch):
    """Bare-metal regression — when deploy mode defaults to systemd,
    the original systemctl path runs unchanged."""
    monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("STATION_AGENT_LAUNCHER_URL", raising=False)

    mock_systemctl = AsyncMock(return_value={"success": True, "stdout": "", "stderr": "", "returncode": 0})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        resp = await client.post("/api/runs/trigger")

    assert resp.status_code == 200
    assert resp.json()["detail"] == "claude-agent.service started"
    mock_systemctl.assert_awaited_once_with("start", "claude-agent.service")


@pytest.mark.asyncio
async def test_trigger_returns_500_when_systemctl_fails(client, monkeypatch):
    monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("STATION_AGENT_LAUNCHER_URL", raising=False)

    mock_systemctl = AsyncMock(return_value={"success": False, "error": "permission denied"})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        resp = await client.post("/api/runs/trigger")

    assert resp.status_code == 500
    assert "permission denied" in resp.json()["detail"]
