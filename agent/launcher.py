"""HTTP launcher for the agent container.

The dashboard cannot reach systemd from inside its own container, so when
running under compose it POSTs to this small FastAPI service to start a run.
The launcher spawns ``run-manager.sh`` as a detached subprocess and returns
immediately — same fire-and-forget shape as ``systemctl start``.

On bare-metal systemd deployments this process is unused; ``trigger_run``
falls back to systemctl when ``STATION_AGENT_LAUNCHER_URL`` is unset.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

RUN_MANAGER = Path(os.environ.get("STATION_RUN_MANAGER", "/app/agent/scripts/run-manager.sh"))
LOG_DIR = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))

app = FastAPI(title="claude-agent-station launcher")

# Track the last-spawned process so a second trigger can report whether one
# is already in flight. Best-effort — if the process exits and we miss it,
# the next trigger just starts a new one.
_current: subprocess.Popen | None = None


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


@app.post("/run")
def trigger() -> dict:
    """Spawn run-manager.sh detached. Returns once the process is forked."""
    global _current

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
