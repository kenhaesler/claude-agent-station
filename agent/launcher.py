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

import glob
import logging
import os
import shutil
import subprocess
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

RUN_MANAGER = Path(os.environ.get("STATION_RUN_MANAGER", "/app/agent/scripts/run-manager.sh"))
LOG_DIR = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))
WORKDIR = os.environ.get("STATION_WORKDIR", "/app")
# Shared secret with the dashboard. When set, /run requires a matching
# X-Launcher-Token header. When unset we accept anonymous calls but log a
# warning at startup — defaulting to closed would break the bare-metal
# systemd path that doesn't go through this launcher at all.
LAUNCHER_TOKEN = os.environ.get("STATION_LAUNCHER_TOKEN", "")
DASHBOARD_BASE_URL = os.environ.get("STATION_DASHBOARD_BASE_URL", "http://localhost:8420").rstrip("/")


def _fetch_gh_token() -> str | None:
    """Fetch a fresh GitHub App installation token from the dashboard.

    Returns None if the dashboard isn't reachable, the App isn't installed,
    or any other failure mode. Best-effort — the run continues regardless.
    """
    headers = {}
    if LAUNCHER_TOKEN:
        headers["X-Launcher-Token"] = LAUNCHER_TOKEN
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{DASHBOARD_BASE_URL}/api/github/app/token", headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Dashboard auth fetch failed: %s", exc)
        return None
    if resp.status_code == 200:
        return resp.json().get("token")
    if resp.status_code == 404:
        # GitHub App not configured yet — silent, this is normal first-run
        return None
    logger.warning("Dashboard auth fetch returned %s: %s", resp.status_code, resp.text[:200])
    return None


# This launcher keeps process state in module globals (``_current``), so it
# only works correctly under a single uvicorn worker. The Dockerfile launches
# without --workers; do not change that without reworking state to be shared.
app = FastAPI(title="claude-agent-station launcher")
_current: subprocess.Popen | None = None


def _ensure_claude_config() -> None:
    """Restore ``/root/.claude.json`` from a backup if missing.

    The Claude Code CLI writes ``~/.claude.json`` itself on first run, but
    when the host's ``~/.claude`` is bind-mounted into the container it can
    arrive without that file (it lives outside the mounted directory on
    the host). The CLI then refuses to start, printing a backup-restore
    hint to stderr. The vision-analyst path was silently swallowing this
    because the orchestrator was already root-blocked too (see
    ``IS_SANDBOX`` in compose.yml).

    We pick the newest ``backups/.claude.json.backup.*`` and copy it into
    place. Idempotent: skipped when the file already exists.
    """
    target = Path("/root/.claude.json")
    if target.exists():
        return
    backups = sorted(glob.glob("/root/.claude/backups/.claude.json.backup.*"))
    if not backups:
        logger.warning(
            "launcher: /root/.claude.json missing and no backup found under "
            "/root/.claude/backups — Claude CLI calls will fail. Mount the host's "
            "Claude config or run ``claude`` once interactively to generate one.",
        )
        return
    src = backups[-1]
    try:
        shutil.copy2(src, target)
        os.chmod(target, 0o600)
        logger.info("launcher: restored /root/.claude.json from %s", src)
    except OSError as exc:
        logger.warning("launcher: failed to restore /root/.claude.json: %s", exc)


_ensure_claude_config()

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


class RunHint(BaseModel):
    hint_run_id: str | None = None


def _spawn_run_manager(hint_run_id: str | None = None) -> dict:
    """Fork run-manager.sh detached and return immediately.

    ``hint_run_id`` is propagated to the subprocess as
    ``STATION_RUN_ID_OVERRIDE`` so the bash script adopts the pre-allocated
    run_id from the dashboard instead of generating its own.
    """
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

    # Fetch GH_TOKEN from the dashboard. Best-effort — never fail the run
    # because of an auth fetch problem.
    env = os.environ.copy()
    gh_token = _fetch_gh_token()
    if gh_token:
        env["GH_TOKEN"] = gh_token

    # Bump the SDK's stream-close timeout from the 60s default. After the
    # bundled CLI emits its first ResultMessage the SDK begins a countdown
    # before closing stdin; once stdin closes, every PreToolUse /
    # PostToolUse hook callback the CLI tries to make to the Python side
    # raises ``Error: Stream closed`` (cli.js:7552 sendRequest).
    # Production hit this ~1-2 minutes into a long Agent Teams session
    # — teammates' tool calls were still happening but their hooks
    # silently failed, so audit_log rows stopped being written and
    # teammates produced no commits. 30 minutes is generous enough for
    # multi-issue Agent Teams runs without leaving stdin open
    # indefinitely. Operators can override via the env if they need
    # longer.
    env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")

    # Propagate the pre-allocated run_id hint so run-manager.sh adopts it
    # via STATION_RUN_ID_OVERRIDE, converging on the placeholder row the
    # dashboard already inserted.
    if hint_run_id:
        env["STATION_RUN_ID_OVERRIDE"] = hint_run_id

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "launcher.out"
    log_fh = log_path.open("ab")

    _current = subprocess.Popen(
        ["bash", str(RUN_MANAGER)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=WORKDIR,
        env=env,
    )
    logger.info("Spawned run-manager.sh pid=%s, logging to %s, app_auth=%s, hint_run_id=%s",
                _current.pid, log_path, "yes" if gh_token else "no",
                hint_run_id or "none")
    return {"status": "triggered", "pid": _current.pid, "log": str(log_path)}


@app.post("/run")
def trigger(
    body: RunHint | None = None,
    x_launcher_token: str | None = Header(default=None),
) -> dict:
    """Spawn run-manager.sh detached. Returns once the process is forked.

    Accepts an optional JSON body ``{"hint_run_id": "run-..."}`` which is
    propagated to run-manager.sh as ``STATION_RUN_ID_OVERRIDE`` so the
    script adopts the pre-allocated run_id from the dashboard's placeholder
    row instead of generating its own timestamp-based id.

    Before spawning, fetch a fresh GitHub App installation token from the
    dashboard and export it as GH_TOKEN in the subprocess env. Lets the
    `gh` CLI (and any tools that read GH_TOKEN) act as the App's
    installation. If the dashboard isn't reachable or GitHub isn't
    configured, the run still proceeds — the agent will fall back to
    whatever auth gh already has (e.g. host bind mount on systemd).
    """
    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    hint = body.hint_run_id if body else None
    return _spawn_run_manager(hint_run_id=hint)


_current_analyst: subprocess.Popen | None = None


@app.get("/vision-analyst/status")
def vision_analyst_status() -> dict:
    running = _current_analyst is not None and _current_analyst.poll() is None
    return {
        "running": running,
        "pid": _current_analyst.pid if running else None,
        "exit_code": _current_analyst.returncode if (_current_analyst and not running) else None,
    }


@app.post("/vision-analyst")
def trigger_vision_analyst(
    project_id: int,
    x_launcher_token: str | None = Header(default=None),
) -> dict:
    """Spawn `python -m agent.vision_analyst --project-id <id>` detached."""
    global _current_analyst

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    if _current_analyst is not None and _current_analyst.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"vision-analyst already running (pid={_current_analyst.pid})",
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"vision-analyst-{project_id}.out"
    log_fh = log_path.open("ab")

    env = os.environ.copy()
    gh_token = _fetch_gh_token()
    if gh_token:
        env["GH_TOKEN"] = gh_token

    _current_analyst = subprocess.Popen(
        ["python", "-m", "agent.vision_analyst", "--project-id", str(project_id)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=WORKDIR,
        env=env,
    )
    logger.info(
        "Spawned vision_analyst pid=%s, project_id=%s, log=%s, app_auth=%s",
        _current_analyst.pid, project_id, log_path, "yes" if gh_token else "no",
    )
    return {"status": "triggered", "pid": _current_analyst.pid, "log": str(log_path)}
