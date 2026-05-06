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

from app.services.systemd import systemctl

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
