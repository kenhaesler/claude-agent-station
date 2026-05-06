"""Tests for the deploy-mode-aware service control facade."""

from __future__ import annotations

import pytest

from unittest.mock import AsyncMock, patch

import respx


@pytest.mark.asyncio
async def test_mode_default_is_systemd(monkeypatch):
    monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)
    from app.services import service_control
    assert service_control._mode() == "systemd"


@pytest.mark.asyncio
async def test_mode_reads_env_lowercase(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "COMPOSE")
    from app.services import service_control
    assert service_control._mode() == "compose"


@pytest.mark.asyncio
async def test_start_systemd_mode_calls_systemctl(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control

    mock_systemctl = AsyncMock(return_value={"success": True, "stdout": "", "stderr": "", "returncode": 0})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        result = await service_control.start_agent_service()

    assert result["success"] is True
    mock_systemctl.assert_awaited_once_with("start", "claude-agent.service")


@pytest.mark.asyncio
async def test_start_compose_mode_posts_to_launcher(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control

    with respx.mock() as mock:
        mock.post("http://agent:8421/run").respond(200, json={"status": "triggered", "pid": 42})
        result = await service_control.start_agent_service()

    assert result["success"] is True
    assert result["pid"] == 42


@pytest.mark.asyncio
async def test_start_compose_mode_forwards_token(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "tok-1")
    from app.services import service_control

    with respx.mock() as mock:
        route = mock.post("http://agent:8421/run").respond(200, json={"status": "triggered"})
        await service_control.start_agent_service()

    assert route.calls[0].request.headers["X-Launcher-Token"] == "tok-1"


@pytest.mark.asyncio
async def test_start_compose_mode_unreachable_returns_error(monkeypatch):
    import httpx
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control

    with respx.mock() as mock:
        mock.post("http://agent:8421/run").mock(side_effect=httpx.ConnectError("refused"))
        result = await service_control.start_agent_service()

    assert result["success"] is False
    assert "launcher unreachable" in result["error"].lower()


@pytest.mark.asyncio
async def test_launcher_response_cannot_override_success_or_status_code(monkeypatch):
    """Even if a launcher response includes 'success' or 'status_code' keys
    in its JSON body, the HTTP-derived values must win — otherwise a
    misbehaving launcher could mislead the dashboard."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control

    with respx.mock() as mock:
        mock.post("http://agent:8421/run").respond(
            200,
            json={"success": "no", "status_code": 999, "pid": 7},
        )
        result = await service_control.start_agent_service()

    assert result["success"] is True   # from HTTP 200, not body's "no"
    assert result["status_code"] == 200
    assert result["pid"] == 7          # body fields still come through
