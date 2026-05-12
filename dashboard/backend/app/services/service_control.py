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
import subprocess

import httpx

from app.services.systemd import get_service_status as systemd_get_status, systemctl

logger = logging.getLogger(__name__)

DEFAULT_AGENT_UNIT = "claude-agent.service"


_VALID_DEPLOY_MODES = ("systemd", "compose")


def deploy_mode() -> str:
    """Return the active deploy mode — exactly ``"systemd"`` or ``"compose"``.

    Public API — other services that need to branch on the deploy shape
    (e.g. the stale-run reaper) should call this instead of reading the
    env var directly so the dispatch decision lives in one place. The
    return value is also surfaced by ``/api/system/status`` and typed as
    ``'systemd' | 'compose'`` on the frontend, so anything outside that
    set falls back to ``"systemd"`` rather than leaking through.
    """
    raw = os.environ.get("STATION_DEPLOY_MODE", "systemd").lower()
    return raw if raw in _VALID_DEPLOY_MODES else "systemd"


# Alias retained for the existing internal call sites; new code should use
# :func:`deploy_mode`. Removing this alias would force a wider patch in
# this file's helpers; kept private to discourage external use.
_mode = deploy_mode


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


async def _launcher_call(method: str, path: str,
                         json_body: dict | None = None) -> dict:
    """Call the agent launcher and shape the response like systemctl()."""
    base = _launcher_base_url()
    if not base:
        return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url,
                                        headers=_launcher_headers(),
                                        json=json_body)
    except httpx.HTTPError as exc:
        # 502 Bad Gateway is the right HTTP status for "upstream
        # unreachable"; trigger_run preserves status_code so the dashboard
        # response code matches the contract documented in the plan.
        return {
            "success": False,
            "error": f"launcher unreachable: {exc}",
            "status_code": 502,
        }

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


async def start_agent_service(hint_run_id: str | None = None) -> dict:
    """Start the agent (systemctl start, or POST /run on the launcher).

    ``hint_run_id`` lets the dashboard pre-allocate a run_id so the
    in-flight run row created on /api/runs/trigger and the bash-emitted
    run_start webhook converge on the same id. The launcher passes this
    to run-manager.sh as ``STATION_RUN_ID_OVERRIDE``.
    """
    if _mode() == "compose":
        body = {"hint_run_id": hint_run_id} if hint_run_id else None
        return await _launcher_call("POST", "/run", json_body=body)
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

    ``service_active`` is True when **any** child process the agent
    container is responsible for is in flight — currently either
    ``run-manager.sh`` (full orchestrator runs) or ``vision_analyst``
    (Hook 3 vision-bootstrap). Without the second source, the stale-run
    reaper saw ``service_active=False`` while a vision-analyst was
    running, marked the in-flight Run row ``interrupted``, then the
    analyst's terminal webhook flipped it back to ``completed`` minutes
    later — producing the confusing ``interrupted → completed`` flicker
    operators noticed.
    """
    if _mode() == "compose":
        run_status = await _launcher_call("GET", "/status")
        analyst_status = await _launcher_call("GET", "/vision-analyst/status")
        run_running = bool(run_status.get("running"))
        analyst_running = bool(analyst_status.get("running"))
        # ``pid`` semantically reports a single in-flight process; prefer
        # the orchestrator's pid when both are running because the
        # orchestrator is the more visible workload.
        pid = run_status.get("pid") or analyst_status.get("pid")
        # Surface the first non-success error so the dashboard can
        # surface auth/network problems regardless of which endpoint
        # failed.
        error = None
        if not run_status.get("success"):
            error = run_status.get("error")
        elif not analyst_status.get("success"):
            error = analyst_status.get("error")
        return {
            "service_active": run_running or analyst_running,
            "timer_active": False,
            "timer_next": None,
            "service_stdout": "",
            "timer_stdout": "",
            "pid": pid,
            "error": error,
            # Per-source breakdown — useful for the dashboard's UI when
            # we want to differentiate "an orchestrator run is going" vs
            # "a vision-bootstrap is going". Reaper only checks the
            # combined ``service_active``.
            "run_active": run_running,
            "vision_analyst_active": analyst_running,
        }
    # systemd mode — normalise to the compose shape so callers don't have to
    # branch on deploy mode. The systemd path doesn't have a single pid (the
    # service can have a tree of children), and there's no async error to
    # surface, so both default to None. ``run_active`` mirrors
    # ``service_active`` because systemd doesn't expose vision-analyst as
    # a distinct unit; ``vision_analyst_active`` defaults False.
    result = await systemd_get_status()
    return {
        **result,
        "pid": None,
        "error": None,
        "run_active": bool(result.get("service_active")),
        "vision_analyst_active": False,
    }


async def run_action(action: str, unit: str | None = None) -> dict:
    """Generic service action — used by the system router which exposes
    arbitrary {start|stop|restart|status|enable|disable} on a unit.

    In compose mode we only honour start/stop/status (the only verbs the
    launcher implements); other actions return a 501-shaped error so the
    UI can show a clear message instead of a 500.
    """
    if _mode() == "compose":
        if action == "start":
            return await start_agent_service()
        if action == "stop":
            return await stop_agent_service()
        if action == "status":
            status = await get_agent_status()
            # Reflect launcher reachability: a `status` call that couldn't reach
            # the launcher should surface as failure so the system router raises
            # instead of returning HTTP 200 with the error buried in the body.
            # `error` is None on a successful call (both systemd and compose),
            # populated with a message when something went wrong.
            return {**status, "success": status.get("error") is None}
        return {
            "success": False,
            "status_code": 501,
            "error": f"Action '{action}' is not supported in compose mode",
        }
    return await systemctl(action, unit or DEFAULT_AGENT_UNIT)


async def start_vision_analyst(project_id: int) -> dict:
    """Trigger the vision_analyst (compose: launcher; systemd: transient unit)."""
    if _mode() == "compose":
        base = _launcher_base_url()
        if not base:
            return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
        url = f"{base.rstrip('/')}/vision-analyst?project_id={project_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=_launcher_headers())
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"launcher unreachable: {exc}", "status_code": 502}
        body = {}
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return {**body, "success": 200 <= resp.status_code < 300, "status_code": resp.status_code}

    # systemd: spawn a transient unit
    cmd = [
        "sudo", "systemd-run", "--unit", f"claude-agent-vision-analyst-{project_id}",
        "--user", os.environ.get("STATION_SERVICE_USER", "claude-agent"),
        "python", "-m", "agent.vision_analyst", "--project-id", str(project_id),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return {
        "success": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "status_code": 200 if proc.returncode == 0 else 500,
    }
