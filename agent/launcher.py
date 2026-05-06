"""HTTP launcher for the agent container.

The dashboard cannot reach systemd from inside its own container, so when
running under compose it POSTs to this small FastAPI service to start a run.
The launcher spawns ``run-manager.sh`` as a detached subprocess and returns
immediately — same fire-and-forget shape as ``systemctl start``.

On bare-metal systemd deployments this process is unused; ``trigger_run``
falls back to systemctl when ``STATION_DEPLOY_MODE`` is ``systemd`` (the
default) — see :mod:`app.services.service_control` for the dispatch.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

RUN_MANAGER = Path(os.environ.get("STATION_RUN_MANAGER", "/app/agent/scripts/run-manager.sh"))
LOG_DIR = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))
# Shared secret with the dashboard. When set, /run requires a matching
# X-Launcher-Token header. When unset we accept anonymous calls but log a
# warning at startup — defaulting to closed would break the bare-metal
# systemd path that doesn't go through this launcher at all.
LAUNCHER_TOKEN = os.environ.get("STATION_LAUNCHER_TOKEN", "")

# This launcher keeps process state in module globals (``_current``), so it
# only works correctly under a single uvicorn worker. The Dockerfile launches
# without --workers; do not change that without reworking state to be shared.
app = FastAPI(title="claude-agent-station launcher")
_current: subprocess.Popen | None = None

if not LAUNCHER_TOKEN:
    logger.warning(
        "launcher: STATION_LAUNCHER_TOKEN unset — /run accepts anonymous calls. "
        "Set this env on both the agent and dashboard for production-shaped use."
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    running = _current is not None and _current.poll() is None
    return {
        "running": running,
        "pid": _current.pid if running else None,
        "exit_code": _current.returncode if (_current and not running) else None,
    }


@app.post("/stop")
def stop(x_launcher_token: str | None = Header(default=None)) -> dict:
    """Send SIGTERM to the running run-manager.sh, if any.

    Returns 409 if no run is in flight. The dashboard's service_control
    module calls this in compose mode where ``systemctl stop`` is unavailable.
    """
    global _current

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    if _current is None or _current.poll() is not None:
        raise HTTPException(status_code=409, detail="No run is currently running")

    pid = _current.pid
    _current.terminate()
    logger.info("Sent SIGTERM to run-manager.sh pid=%s", pid)
    return {"status": "stopping", "pid": pid}


@app.post("/run")
def trigger(x_launcher_token: str | None = Header(default=None)) -> dict:
    """Spawn run-manager.sh detached. Returns once the process is forked."""
    global _current

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    if _current is not None and _current.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"A run is already in progress (pid={_current.pid})",
        )

    if not RUN_MANAGER.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"run-manager.sh not found at {RUN_MANAGER}",
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "launcher.out"
    log_fh = log_path.open("ab")

    _current = subprocess.Popen(
        ["bash", str(RUN_MANAGER)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd="/app",
    )
    logger.info("Spawned run-manager.sh pid=%s, logging to %s", _current.pid, log_path)
    return {"status": "triggered", "pid": _current.pid, "log": str(log_path)}
