"""Tests for the system router.

Covers:
- GET /api/system/status — returns system status (mocked systemd calls)
- POST /api/system/service/{action} — valid and invalid actions (mocked systemctl)
- GET /api/system/auth — auth status (mocked credentials file)
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


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


# ---------------------------------------------------------------------------
# GET /api/system/status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_status(client):
    """GET /api/system/status should return service and timer status."""
    mock_svc = {
        "service_active": True,
        "timer_active": True,
        "timer_next": "Mon 2026-03-16 04:00:00 UTC",
        "service_stdout": "",
        "timer_stdout": "",
    }
    mock_resources = {
        "memory_total_mb": 8192.0,
        "memory_available_mb": 4096.0,
        "load_avg": [0.5, 0.3, 0.2],
    }
    with patch("app.routers.system.service_control.get_agent_status", new_callable=AsyncMock, return_value=mock_svc):
        with patch("app.routers.system.get_system_resources", new_callable=AsyncMock, return_value=mock_resources):
            resp = await client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"]["active"] is True
    assert data["timer"]["active"] is True
    assert data["timer"]["next_trigger"] == "Mon 2026-03-16 04:00:00 UTC"
    assert data["resources"]["memory_total_mb"] == 8192.0


@pytest.mark.asyncio
async def test_system_status_inactive(client):
    """GET /api/system/status should return inactive when service is down."""
    mock_svc = {
        "service_active": False,
        "timer_active": False,
        "timer_next": None,
        "service_stdout": "",
        "timer_stdout": "",
    }
    with patch("app.routers.system.service_control.get_agent_status", new_callable=AsyncMock, return_value=mock_svc):
        with patch("app.routers.system.get_system_resources", new_callable=AsyncMock, return_value={}):
            resp = await client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"]["active"] is False
    assert data["timer"]["active"] is False


# ---------------------------------------------------------------------------
# POST /api/system/service/{action}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_action_valid(client):
    """POST /api/system/service/restart should succeed with mocked systemctl."""
    mock_result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
    with patch("app.routers.system.service_control.run_action", new_callable=AsyncMock, return_value=mock_result) as mock_action:
        resp = await client.post("/api/system/service/restart")
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "restart"
    assert data["unit"] == "claude-agent.service"
    mock_action.assert_awaited_once_with("restart", "claude-agent.service")


@pytest.mark.asyncio
async def test_service_action_custom_unit(client):
    """POST /api/system/service/start should accept custom unit query param."""
    mock_result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
    with patch("app.routers.system.service_control.run_action", new_callable=AsyncMock, return_value=mock_result) as mock_action:
        resp = await client.post("/api/system/service/start?unit=claude-agent.timer")
    assert resp.status_code == 200
    assert resp.json()["unit"] == "claude-agent.timer"
    mock_action.assert_awaited_once_with("start", "claude-agent.timer")


@pytest.mark.asyncio
async def test_service_action_invalid(client):
    """POST /api/system/service/destroy should return 400 for invalid action."""
    resp = await client.post("/api/system/service/destroy")
    assert resp.status_code == 400
    assert "Action not allowed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_service_action_failure(client):
    """POST /api/system/service/stop should return 500 when systemctl fails."""
    mock_result = {"success": False, "error": "Permission denied"}
    with patch("app.routers.system.service_control.run_action", new_callable=AsyncMock, return_value=mock_result) as mock_action:
        resp = await client.post("/api/system/service/stop")
    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]
    mock_action.assert_awaited_once_with("stop", "claude-agent.service")


# ---------------------------------------------------------------------------
# GET /api/system/auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_status_no_credentials(client):
    """GET /api/system/auth should return logged_in=False when no creds file."""
    with patch("app.routers.system.os.path.exists", return_value=False):
        resp = await client.get("/api/system/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is False
    assert data["expired"] is True


@pytest.mark.asyncio
async def test_auth_status_valid_credentials(client):
    """GET /api/system/auth should return logged_in=True for valid creds."""
    import time
    future_ms = int((time.time() + 86400) * 1000)  # 24h from now
    creds = {"claudeAiOauth": {"expiresAt": future_ms}}
    with patch("app.routers.system.os.path.exists", return_value=True):
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            with patch("app.routers.system.json.load", return_value=creds):
                resp = await client.get("/api/system/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is True
    assert data["expired"] is False
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_auth_status_expired_credentials(client):
    """GET /api/system/auth should return expired=True for expired creds."""
    past_ms = 1000000  # long ago
    creds = {"claudeAiOauth": {"expiresAt": past_ms}}
    with patch("app.routers.system.os.path.exists", return_value=True):
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            with patch("app.routers.system.json.load", return_value=creds):
                resp = await client.get("/api/system/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is True
    assert data["expired"] is True


@pytest.mark.asyncio
async def test_auth_status_no_expires_at(client):
    """GET /api/system/auth should handle creds without expiresAt."""
    creds = {"claudeAiOauth": {"token": "abc"}}
    with patch("app.routers.system.os.path.exists", return_value=True):
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            with patch("app.routers.system.json.load", return_value=creds):
                resp = await client.get("/api/system/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is True
    assert data["expired"] is False
    assert data["expires_at"] is None


@pytest.mark.asyncio
async def test_auth_status_corrupted_file(client):
    """GET /api/system/auth should handle exceptions gracefully."""
    with patch("app.routers.system.os.path.exists", return_value=True):
        with patch("builtins.open", side_effect=PermissionError("denied")):
            resp = await client.get("/api/system/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is False
    assert "error" in data
