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


@pytest.mark.asyncio
async def test_stop_systemd_mode_calls_systemctl(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control
    mock_systemctl = AsyncMock(return_value={"success": True})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        await service_control.stop_agent_service()
    mock_systemctl.assert_awaited_once_with("stop", "claude-agent.service")


@pytest.mark.asyncio
async def test_stop_compose_mode_posts_to_launcher(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control
    with respx.mock() as mock:
        mock.post("http://agent:8421/stop").respond(200, json={"status": "stopping", "pid": 7})
        result = await service_control.stop_agent_service()
    assert result["success"] is True
    assert result["pid"] == 7


@pytest.mark.asyncio
async def test_status_systemd_uses_systemd_get_service_status(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control
    mock = AsyncMock(return_value={"service_active": True, "timer_active": False})
    with patch("app.services.service_control.systemd_get_status", mock):
        result = await service_control.get_agent_status()
    assert result["service_active"] is True


@pytest.mark.asyncio
async def test_status_compose_translates_launcher_status(monkeypatch):
    """In compose mode, /status returns {running, pid, exit_code} — translate
    to the systemd-shaped {service_active, ...} the dashboard already
    consumes so existing UI code keeps working."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control
    with respx.mock() as mock:
        mock.get("http://agent:8421/status").respond(200, json={"running": True, "pid": 99, "exit_code": None})
        result = await service_control.get_agent_status()
    assert result["service_active"] is True
    assert result["pid"] == 99


@pytest.mark.asyncio
async def test_status_compose_when_unreachable_returns_inactive(monkeypatch):
    import httpx
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control
    with respx.mock() as mock:
        mock.get("http://agent:8421/status").mock(side_effect=httpx.ConnectError("refused"))
        result = await service_control.get_agent_status()
    assert result["service_active"] is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_status_returns_same_keys_in_both_modes(monkeypatch):
    """Drop-in substitution: callers should be able to read any of the 7
    keys without branching on deploy mode."""
    expected_keys = {
        "service_active", "timer_active", "timer_next",
        "service_stdout", "timer_stdout", "pid", "error",
    }

    # Systemd mode
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control
    systemd_mock = AsyncMock(return_value={
        "service_active": True,
        "timer_active": True,
        "timer_next": "Mon 2026-03-16 04:00:00 UTC",
        "service_stdout": "ok",
        "timer_stdout": "ok",
    })
    with patch("app.services.service_control.systemd_get_status", systemd_mock):
        systemd_result = await service_control.get_agent_status()
    assert set(systemd_result.keys()) == expected_keys
    assert systemd_result["pid"] is None
    assert systemd_result["error"] is None

    # Compose mode
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    with respx.mock() as mock:
        mock.get("http://agent:8421/status").respond(200, json={"running": True, "pid": 1, "exit_code": None})
        compose_result = await service_control.get_agent_status()
    assert set(compose_result.keys()) == expected_keys


@pytest.mark.asyncio
async def test_run_action_compose_unsupported_action_returns_501(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    from app.services import service_control
    result = await service_control.run_action("enable", "claude-agent.timer")
    assert result["success"] is False
    assert result["status_code"] == 501
    assert "compose mode" in result["error"]


@pytest.mark.asyncio
async def test_run_action_status_compose_unreachable_surfaces_as_failure(monkeypatch):
    """When the launcher is unreachable, run_action('status') must report
    success=False so the system router raises instead of returning 200."""
    import httpx
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control

    with respx.mock() as mock:
        mock.get("http://agent:8421/status").mock(side_effect=httpx.ConnectError("refused"))
        result = await service_control.run_action("status", "claude-agent.service")

    assert result["success"] is False
    assert "launcher unreachable" in result["error"].lower()
    assert result["service_active"] is False


@pytest.mark.asyncio
async def test_run_action_status_compose_reachable_inactive_is_success(monkeypatch):
    """Conversely, a reachable launcher reporting an inactive service is
    NOT an error — the call succeeded, the agent is just idle."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control

    with respx.mock() as mock:
        mock.get("http://agent:8421/status").respond(200, json={"running": False, "pid": None, "exit_code": None})
        result = await service_control.run_action("status", "claude-agent.service")

    assert result["success"] is True       # call succeeded
    assert result["service_active"] is False  # but agent is idle
    assert result["error"] is None
