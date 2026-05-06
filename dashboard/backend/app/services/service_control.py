"""Deploy-mode-aware service control.

In ``systemd`` mode (the default, bare-metal install), service actions are
``sudo systemctl <action> claude-agent.service`` calls. In ``compose`` mode,
they go to the agent container's HTTP launcher instead — the dashboard
container has no systemd, so it can't shell out to systemctl.

Selected by ``STATION_DEPLOY_MODE`` env (``systemd`` | ``compose``).
The launcher base URL is ``STATION_AGENT_LAUNCHER_URL`` (e.g.
``http://agent:8421``); the optional shared secret is ``STATION_LAUNCHER_TOKEN``.
"""

from __future__ import annotations

import logging
import os

import httpx

from app.services.systemd import get_service_status as systemd_get_status, systemctl

logger = logging.getLogger(__name__)

DEFAULT_AGENT_UNIT = "claude-agent.service"


def _mode() -> str:
    return os.environ.get("STATION_DEPLOY_MODE", "systemd").lower()


def _launcher_base_url() -> str | None:
    return os.environ.get("STATION_AGENT_LAUNCHER_URL")


def _launcher_token() -> str | None:
    val = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    return val if val else None


def _launcher_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = _launcher_token()
    if token:
        headers["X-Launcher-Token"] = token
    return headers


async def _launcher_call(method: str, path: str) -> dict:
    """Call the agent launcher and shape the response like systemctl()."""
    base = _launcher_base_url()
    if not base:
        return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url, headers=_launcher_headers())
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"launcher unreachable: {exc}"}

    body: dict = {}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return {
        **body,
        "success": 200 <= resp.status_code < 300,
        "status_code": resp.status_code,
    }


async def start_agent_service() -> dict:
    """Start the agent (systemctl start, or POST /run on the launcher)."""
    if _mode() == "compose":
        return await _launcher_call("POST", "/run")
    return await systemctl("start", DEFAULT_AGENT_UNIT)


async def stop_agent_service() -> dict:
    """Stop the agent (systemctl stop, or POST /stop on the launcher)."""
    if _mode() == "compose":
        return await _launcher_call("POST", "/stop")
    return await systemctl("stop", DEFAULT_AGENT_UNIT)


async def get_agent_status() -> dict:
    """Return service-active status with a shape compatible with the existing
    systemd path: ``{"service_active": bool, "timer_active": bool, ...}``.

    In compose mode the agent has no timer (the launcher is always up), so
    ``timer_active`` is always False.
    """
    if _mode() == "compose":
        result = await _launcher_call("GET", "/status")
        running = bool(result.get("running"))
        return {
            "service_active": running,
            "timer_active": False,
            "timer_next": None,
            "service_stdout": "",
            "timer_stdout": "",
            "pid": result.get("pid"),
            "error": None if result.get("success") else result.get("error"),
        }
    # systemd mode — normalise to the compose shape so callers don't have to
    # branch on deploy mode. The systemd path doesn't have a single pid (the
    # service can have a tree of children), and there's no async error to
    # surface, so both default to None.
    result = await systemd_get_status()
    return {**result, "pid": None, "error": None}
