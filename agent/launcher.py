"""HTTP launcher for the agent container.

The dashboard cannot reach systemd from inside its own container, so when
running under compose it POSTs to this small FastAPI service to start a run.
The launcher spawns ``python -m agent.station_orchestrator --driver`` as a
detached subprocess and returns immediately — same fire-and-forget shape as
``systemctl start``.

On bare-metal systemd deployments this process is unused; ``trigger_run``
falls back to systemctl when ``STATION_DEPLOY_MODE`` is ``systemd`` (the
default) — see :mod:`app.services.service_control` for the dispatch.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import docker as _docker_sdk

from agent.launcher_reaper import reaper_loop
from agent.runner_spawn import RunnerHandle  # noqa: F401
from agent.runner_spawn import spawn_runner  # noqa: F401

import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

LOG_DIR = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))
WORKDIR = os.environ.get("STATION_WORKDIR", "/app")
# Config + workspaces paths the Python driver needs as CLI args.
STATION_CONFIG = os.environ.get(
    "STATION_CONFIG", "/home/claude-agent/.claude/autonomous/manager-config.json"
)
STATION_WORKSPACES = os.environ.get(
    "STATION_WORKSPACES", "/home/claude-agent/workspaces"
)
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
# Module-level reference to the zombie reaper task. Without this,
# asyncio.create_task()'s return value would be the ONLY strong
# reference; Python's GC can collect a task with no strong refs,
# silently stopping the background loop. See #372.
_reaper_task: asyncio.Task | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start/stop the background zombie reaper. Replaces the deprecated
    ``@app.on_event("startup")`` hook (which had a documented bug where
    fire-and-forget tasks could be GC'd; we hold an explicit reference
    here as well as a belt-and-suspenders measure).

    In container mode, starts the container-aware reaper_loop (launcher_reaper).
    In inline mode, also starts the legacy subprocess reaper (_zombie_reaper)
    to cover any inline runs.
    """
    global _reaper_task
    _reaper_task = asyncio.create_task(reaper_loop())
    logger.info(
        "Container reaper started (from launcher_reaper). Zombie reaper (inline) "
        "interval=%ds, timeout=%ds",
        ZOMBIE_CHECK_INTERVAL_SECONDS, ZOMBIE_TIMEOUT_SECONDS,
    )
    try:
        yield
    finally:
        if _reaper_task and not _reaper_task.done():
            _reaper_task.cancel()
            try:
                await _reaper_task
            except (asyncio.CancelledError, Exception):
                pass
        _reaper_task = None


app = FastAPI(title="claude-agent-station launcher", lifespan=_lifespan)
_current: subprocess.Popen | None = None

_runners: dict[str, RunnerHandle] = {}

_docker_client = None


def _get_docker_client():
    """Return a process-wide ``docker.from_env()`` client (lazy singleton).

    Scope is the uvicorn worker process. The Dockerfile launches with a
    single worker (see ``_lifespan`` notes above), so this singleton is
    safe; if that ever changes, swap to a per-worker factory.
    """
    global _docker_client
    if _docker_client is None:
        _docker_client = _docker_sdk.from_env()
    return _docker_client


# STATION_* env vars copied into the runner container.
#
# Earlier iterations of this whitelist (PR #419 review) excluded the
# webhook/API secrets to "prevent leaking secrets into runners". That
# reasoning was wrong: the runner IS the orchestrator process — it has
# to authenticate back to the dashboard, and its trust level is identical
# to the launcher's. Excluding the secrets just produced 401 on every
# webhook the runner emitted and the orchestrator aborted before doing
# anything useful. See post-#386 PR-3 end-to-end repro.
#
# What we still NEVER copy:
#   - ``STATION_LAUNCHER_TOKEN`` — that token authenticates the
#     dashboard → launcher hop; the runner has no reason to call its own
#     launcher endpoint, so propagating it would expand the blast
#     radius of a runner compromise without enabling any feature.
#   - Anything outside the ``STATION_*`` namespace, with the documented
#     exception of ``IS_SANDBOX`` (Claude CLI requires it under root —
#     see compose.yml's agent service for the rationale).
_RUNNER_ENV_WHITELIST: frozenset[str] = frozenset({
    "STATION_DB_URL",
    "STATION_DB_PASSWORD_FILE",
    "STATION_CONFIG",
    "STATION_WORKSPACES",
    "STATION_AGENT_LAUNCHER_URL",
    "STATION_LOG_DIR",
    "STATION_DASHBOARD_BASE_URL",
    "STATION_WEBHOOK_URL",
    "STATION_DEPLOY_MODE",
    # Runner → dashboard auth — without these the orchestrator's webhooks
    # and /api/* calls 401 and the run aborts in preflight.
    "STATION_WEBHOOK_SECRET",
    "STATION_API_KEY",
    "STATION_GITHUB_WEBHOOK_SECRET",
    # Not STATION_-prefixed but required: Claude CLI refuses
    # ``--dangerously-skip-permissions`` under root unless this is set.
    "IS_SANDBOX",
})


def _normalize_memory(value: object) -> str | int | None:
    """Coerce a memory quota into a form the Docker SDK accepts.

    Project.runner_memory_limit is stored as Integer bytes (#386 PR-1),
    while ``settings.default_runner_memory_limit`` is a unit-suffixed
    string like ``"2g"``. Docker SDK ``mem_limit`` accepts both an int
    (bytes) or a str with a unit suffix. Pass each through verbatim.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return str(value)


def _normalize_cpus(value: object) -> str | None:
    """Coerce a cpu quota into a decimal string (e.g. ``"0.5"``).

    Project.runner_cpu_limit is a Float; the default is a string. The
    downstream ``_cpus_to_nano`` converter calls ``float(...)`` so either
    is fine, but normalizing here keeps the contract with spawn_runner
    string-typed and self-documenting.
    """
    if value is None:
        return None
    return str(value)


async def _resolve_quotas(project_repo: str | None) -> dict:
    """Look up per-project quotas; fall back to settings defaults.

    Async because it queries the dashboard DB via SQLAlchemy's async
    session. Called inside the FastAPI event loop — do NOT wrap in
    ``asyncio.run()`` (that would crash on the running loop).
    """
    from app.config import settings
    default = {
        "memory": _normalize_memory(settings.default_runner_memory_limit),
        "cpus": _normalize_cpus(settings.default_runner_cpu_limit),
    }
    if project_repo is None:
        return default
    from app.database import async_session
    from app.models import Project
    from sqlalchemy import select

    async with async_session() as db:
        project = (
            await db.execute(select(Project).where(Project.repo == project_repo))
        ).scalar_one_or_none()
    if project is None:
        return default
    return {
        "memory": _normalize_memory(project.runner_memory_limit) or default["memory"],
        "cpus": _normalize_cpus(project.runner_cpu_limit) or default["cpus"],
    }


def _env_passthrough() -> dict:
    """Build the env dict for the spawned runner.

    Default-deny across the ``STATION_*`` namespace via
    :data:`_RUNNER_ENV_WHITELIST`. See that constant's docstring for the
    threat-model reasoning and the list of keys we deliberately exclude.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key in _RUNNER_ENV_WHITELIST
    }


# Destinations on the launcher's container that runners MUST also see.
# These hold credentials (Claude OAuth, gh auth) and the db_password
# secret — operator-level state shared across all runs on the host.
# Without them the runner can't authenticate the Claude CLI, can't run
# ``gh`` commands, and can't open a DB connection. The launcher inspects
# its own container at startup, finds these destinations, and propagates
# the underlying host source paths into each spawned runner.
_INHERITED_MOUNT_DESTINATIONS: frozenset[str] = frozenset({
    "/root/.claude",
    "/root/.config/gh",
    "/run/secrets/db_password",
})

_inherited_mounts: list[dict] | None = None


def _read_own_container_id() -> str | None:
    """Best-effort: return the launcher's own container ID.

    ``/etc/hostname`` is the canonical short ID inside a Docker container.
    Falls back to ``None`` outside Docker (unit tests, bare-metal systemd
    deploys) — callers degrade gracefully when no inherited mounts are
    available.
    """
    try:
        with open("/etc/hostname", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _get_inherited_mounts() -> list[dict]:
    """Inspect the launcher's own container and return the mounts each
    runner inherits.

    Cached in module state so we hit the Docker socket once per uvicorn
    worker, not once per ``/run`` call. Returns ``[]`` outside Docker
    (no container ID, no daemon) so unit tests don't have to mock this
    out — the orchestrator just won't see those mounts.

    The returned dicts match the Docker SDK's ``volumes={}`` kwarg shape
    so :func:`agent.runner_spawn.spawn_runner` can merge them with the
    named-volume mounts it already provides.
    """
    global _inherited_mounts
    if _inherited_mounts is not None:
        return _inherited_mounts

    cid = _read_own_container_id()
    if cid is None:
        _inherited_mounts = []
        return _inherited_mounts

    try:
        own = _get_docker_client().containers.get(cid)
    except Exception as exc:
        # Most commonly: cid doesn't match a container the daemon
        # acknowledges (cgroup-only container, mismatched namespaces).
        logger.warning("launcher: could not inspect own container %s: %s", cid, exc)
        _inherited_mounts = []
        return _inherited_mounts

    mounts: list[dict] = []
    for mount in own.attrs.get("Mounts", []):
        if mount.get("Destination") not in _INHERITED_MOUNT_DESTINATIONS:
            continue
        # Docker SDK accepts ``volumes={host_or_volname: {"bind": dst, "mode": "..."}}``;
        # source for a bind mount is the host path, for a named volume
        # it's the volume name. We treat both the same — the daemon
        # resolves to the same backing storage either way.
        mode = "ro" if not mount.get("RW", True) else "rw"
        mounts.append({
            "source": mount["Source"],
            "destination": mount["Destination"],
            "mode": mode,
        })

    _inherited_mounts = mounts
    if mounts:
        logger.info(
            "launcher: spawned runners will inherit %d mount(s): %s",
            len(mounts), [m["destination"] for m in mounts],
        )
    else:
        logger.warning(
            "launcher: no inherited mounts found on container %s — runners "
            "will lack Claude/gh credentials and may fail at first CLI call",
            cid,
        )
    return mounts


async def _spawn_runner_container(hint_run_id: str, project_repo: str | None) -> dict:
    """Spawn one runner container; record handle; return route payload."""
    from app.config import settings
    if hint_run_id in _runners:
        raise HTTPException(
            status_code=409,
            detail=f"run {hint_run_id} already has a running container",
        )
    client = _get_docker_client()
    quotas = await _resolve_quotas(project_repo)
    env = _env_passthrough()
    # Override the launcher URL for the runner's view. Inside the
    # launcher's own process ``http://localhost:8421`` reaches the
    # FastAPI app via loopback, but inside a spawned runner container
    # ``localhost`` is the runner itself — webhook-tick POSTs would
    # silently fail with connection-refused, the zombie reaper would
    # see no heartbeats, and the runner would be SIGTERM'd after
    # ZOMBIE_TIMEOUT_SECONDS. ``http://agent:8421`` resolves to the
    # launcher's container via compose's DNS on ``agent-net``.
    env["STATION_AGENT_LAUNCHER_URL"] = os.environ.get(
        "STATION_RUNNER_LAUNCHER_URL", "http://agent:8421",
    )
    # Fetch a fresh GitHub App installation token for the runner — the
    # legacy ``_spawn_run_manager`` (inline mode) does the same and the
    # original PR #386 port forgot to carry it over. Without
    # ``GH_TOKEN`` the runner's first ``git clone`` aborts with
    # ``could not read Username for 'https://github.com'`` because the
    # bare HTTPS URL has no credential helper inside the container.
    # Best-effort: a missing token (dashboard unreachable, App not
    # installed) lets the run proceed and fail at clone time with a
    # clearer error than a silent webhook 401 loop.
    gh_token = _fetch_gh_token()
    if gh_token:
        env["GH_TOKEN"] = gh_token
    handle = spawn_runner(
        client,
        hint_run_id=hint_run_id,
        project_repo=project_repo,
        quotas=quotas,
        env_passthrough=env,
        image=settings.runner_image,
        config_path=os.environ.get("STATION_CONFIG", "/var/lib/claude-agent-station/manager-config.json"),
        workspaces_dir=os.environ.get("STATION_WORKSPACES", "/var/lib/claude-agent-station/workspaces"),
        inherited_mounts=_get_inherited_mounts(),
    )
    _runners[hint_run_id] = handle
    return {"status": "triggered", "container": handle.container_name, "run_id": handle.run_id}

# Last time we observed a webhook event for the active subprocess. Used by
# the zombie-reaper task to decide if a still-alive subprocess has gone
# unproductive. None when no run is active. See #360.
_last_webhook_at: datetime | None = None

# Reap subprocesses whose webhook stream has gone silent for this many
# seconds. Generous to avoid false positives during legitimate quiet
# stretches (e.g. `gh issue list` on a slow network). See #360.
ZOMBIE_TIMEOUT_SECONDS = int(os.environ.get("STATION_LAUNCHER_ZOMBIE_TIMEOUT_S", "120"))
ZOMBIE_CHECK_INTERVAL_SECONDS = 30


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
    return {
        "runs": [
            {
                "run_id": h.run_id,
                "container_name": h.container_name,
                "project_repo": h.project_repo,
                "started_at": h.started_at.isoformat(),
                "last_webhook_at": h.last_webhook_at.isoformat(),
            }
            for h in _runners.values()
        ]
    }


# How long to give a runner container's PID 1 to exit gracefully on SIGTERM
# before Docker SIGKILLs it. Kept short so /stop returns promptly to the
# dashboard's polling caller — the orchestrator's signal handlers should
# wrap up in well under this window. See #386 PR-2 review feedback.
RUNNER_STOP_TIMEOUT_SECONDS = int(os.environ.get("STATION_RUNNER_STOP_TIMEOUT_S", "5"))


@app.post("/stop")
async def stop(run_id: str | None = None, x_launcher_token: str | None = Header(default=None)) -> dict:
    """Stop a running container by run_id (container mode) or the active subprocess (inline mode).

    In container mode, pass ``?run_id=<id>`` to identify the container.
    The container is stopped with a short graceful timeout
    (``RUNNER_STOP_TIMEOUT_SECONDS``) so this endpoint stays responsive;
    the Docker call runs in a worker thread so the event loop isn't
    blocked even if the daemon is slow to respond.
    In inline/legacy mode, omitting run_id stops the active subprocess.
    """
    global _current, _last_webhook_at

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    # Container mode: stop by run_id
    if run_id is not None:
        handle = _runners.get(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail=f"no runner for {run_id}")
        client = _get_docker_client()

        def _stop_container() -> None:
            container = client.containers.get(handle.container_name)
            container.stop(timeout=RUNNER_STOP_TIMEOUT_SECONDS)

        try:
            await asyncio.to_thread(_stop_container)
        except Exception as exc:
            logger.warning("stop %s: %s", handle.container_name, exc)
        _runners.pop(run_id, None)
        return {"status": "stopped", "run_id": run_id}

    # Inline/legacy mode: stop active subprocess
    if _current is None or _current.poll() is not None:
        raise HTTPException(status_code=409, detail="No run is currently running")

    pid = _current.pid
    _current.terminate()
    _current = None
    _last_webhook_at = None
    logger.info("Sent SIGTERM to orchestrator pid=%s", pid)
    return {"status": "stopping", "pid": pid}


@app.post("/webhook-tick")
async def webhook_tick(
    run_id: str | None = None,
    x_launcher_token: str | None = Header(None, alias="X-Launcher-Token"),
) -> dict:
    """Bump the heartbeat clock for the named run (container mode) or the active subprocess (inline).

    Called by agent/webhook_emitter.py on every webhook emit. The zombie
    reaper uses this timestamp to identify silent/hung runners.
    """
    global _last_webhook_at
    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    # Container mode: update per-run handle
    if run_id is not None:
        handle = _runners.get(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail=f"no runner for {run_id}")
        handle.last_webhook_at = datetime.now(timezone.utc)
        return {"status": "ok"}

    # Inline/legacy mode: update global timestamp
    if _current is None or _current.poll() is not None:
        return {"ok": True, "stale": True}
    _last_webhook_at = datetime.now(timezone.utc)
    return {"ok": True}


def _reap_once() -> None:
    """One synchronous iteration of the zombie reaper logic.

    Checks whether ``_current`` is alive but has had no webhook activity
    for longer than ``ZOMBIE_TIMEOUT_SECONDS``. If so, sends SIGTERM
    (then SIGKILL if needed) and clears module state. Synchronous so
    it is straightforward to unit-test. See #360.
    """
    global _current, _last_webhook_at

    if _current is None:
        return
    if _current.poll() is not None:
        # Already exited via its own means; clear state.
        _current = None
        _last_webhook_at = None
        return
    if _last_webhook_at is None:
        # No webhook ever arrived — can't measure silence yet.
        return
    age = (datetime.now(timezone.utc) - _last_webhook_at).total_seconds()
    if age < ZOMBIE_TIMEOUT_SECONDS:
        return
    pid = _current.pid
    logger.warning(
        "_zombie_reaper: subprocess pid=%s alive but silent for %.0fs — terminating",
        pid, age,
    )
    try:
        _current.terminate()
        try:
            _current.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("_zombie_reaper: SIGTERM did not exit in 5s — SIGKILL pid=%s", pid)
            _current.kill()
            _current.wait(timeout=2)
    except Exception:
        logger.exception("_zombie_reaper: failed to terminate pid=%s", pid)
    finally:
        _current = None
        _last_webhook_at = None


async def _zombie_reaper() -> None:
    """Background task: if _current is alive but its webhook stream has
    been silent for more than ZOMBIE_TIMEOUT_SECONDS, send SIGTERM,
    wait, then SIGKILL if needed. Clears _current. See #360."""
    while True:
        await asyncio.sleep(ZOMBIE_CHECK_INTERVAL_SECONDS)
        try:
            _reap_once()
        except Exception:
            logger.exception("_zombie_reaper: unexpected error")


class RunHint(BaseModel):
    hint_run_id: str | None = None


def _spawn_run_manager(hint_run_id: str | None = None) -> dict:
    """Fork the orchestrator detached and return immediately.

    Spawns ``python -m agent.station_orchestrator --driver`` (#361/#383).

    ``hint_run_id`` is propagated as ``--run-id`` so the driver adopts the
    pre-allocated run_id from the dashboard instead of minting its own.
    """
    global _current, _last_webhook_at

    if _current is not None and _current.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"A run is already in progress (pid={_current.pid})",
        )

    # Fetch GH_TOKEN from the dashboard. Best-effort — never fail the run
    # because of an auth fetch problem.
    env = os.environ.copy()
    gh_token = _fetch_gh_token()
    if gh_token:
        env["GH_TOKEN"] = gh_token

    # Propagate the pre-allocated run_id hint, converging on the placeholder
    # row the dashboard already inserted.
    if hint_run_id:
        env["STATION_RUN_ID_OVERRIDE"] = hint_run_id

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Per-run log file: matches the path the driver advertises in its
    # run_start webhook (LOG_DIR/run-<RUN_ID>.log). Falls back to
    # launcher.out when no run_id hint is available (legacy systemd callers).
    if hint_run_id:
        run_id_clean = hint_run_id.removeprefix("run-")
        log_path = LOG_DIR / f"run-{run_id_clean}.log"
    else:
        log_path = LOG_DIR / "launcher.out"
    log_fh = log_path.open("ab")

    # The driver argparse requires --run-id; if no hint was supplied we
    # mint one here.
    driver_run_id = hint_run_id or "run-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    cmd = [
        sys.executable, "-m", "agent.station_orchestrator",
        "--driver",
        "--run-id", driver_run_id,
        "--config", STATION_CONFIG,
        "--workspaces-dir", STATION_WORKSPACES,
    ]
    entry_kind = "station_orchestrator --driver (python)"
    # Make the launcher's base URL discoverable so the embedded webhook
    # emitter can ping /webhook-tick from the driver process.
    env.setdefault("STATION_AGENT_LAUNCHER_URL", "http://localhost:8421")

    _current = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=WORKDIR,
        env=env,
    )
    _last_webhook_at = datetime.now(timezone.utc)
    logger.info(
        "Spawned %s pid=%s, logging to %s, app_auth=%s, hint_run_id=%s",
        entry_kind, _current.pid, log_path,
        "yes" if gh_token else "no",
        hint_run_id or "none",
    )
    return {"status": "triggered", "pid": _current.pid, "log": str(log_path)}


@app.post("/run")
async def trigger(
    body: RunHint | None = None,
    x_launcher_token: str | None = Header(default=None),
) -> dict:
    """Spawn the Python orchestrator driver detached. Returns once the process is forked.

    When STATION_RUNNER_MODE=container (default), spawns a Docker container
    via the Docker SDK. When STATION_RUNNER_MODE=inline, falls back to the
    legacy subprocess.Popen path. Keep the inline path for one release window
    so an operator can recover without rolling back a deployment.

    Accepts an optional JSON body ``{"hint_run_id": "run-..."}`` which is
    propagated as ``--run-id`` so the driver adopts the pre-allocated run_id
    from the dashboard's placeholder row instead of generating its own id.
    """
    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    hint = body.hint_run_id if body else None
    from app.config import settings
    mode = os.environ.get("STATION_RUNNER_MODE", settings.runner_mode)
    if mode == "inline":
        return _spawn_run_manager(hint_run_id=hint)
    if not hint:
        hint = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    project_repo = os.environ.get("STATION_PROJECT_REPO")
    return await _spawn_runner_container(hint_run_id=hint, project_repo=project_repo)


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
