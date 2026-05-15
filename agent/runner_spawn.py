"""Spawn one ephemeral runner container per run (#386).

Isolated from the FastAPI route so it can be unit-tested without a live
Docker daemon. The route layer wires together the docker client, the
project quotas (from the DB), and the env passthrough.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class RunnerHandle:
    run_id: str
    container_name: str
    started_at: datetime
    last_webhook_at: datetime
    project_repo: str | None


def _container_name(run_id: str) -> str:
    return f"cas-runner-{run_id.removeprefix('run-')}"


def _cpus_to_nano(cpus: str) -> int:
    return int(float(cpus) * 1_000_000_000)


def spawn_runner(
    client,
    *,
    hint_run_id: str,
    project_repo: str | None,
    quotas: dict,
    env_passthrough: dict,
    image: str,
    config_path: str,
    workspaces_dir: str,
    inherited_mounts: list[dict] | None = None,
) -> RunnerHandle:
    """Spawn a detached, auto-removed runner container.

    ``client`` is a ``docker.from_env()`` instance (or a MagicMock in tests).
    ``quotas`` is ``{"memory": "2g", "cpus": "1.0"}`` — strings come from the
    Project row's columns; defaults are resolved upstream.
    ``env_passthrough`` carries every STATION_* the orchestrator needs.

    ``inherited_mounts`` carries the operator-level bind mounts the
    runner needs to inherit from the launcher's container — Claude OAuth
    creds at ``/root/.claude``, ``gh`` auth at ``/root/.config/gh``, the
    Postgres password secret at ``/run/secrets/db_password``. The
    launcher resolves these by inspecting its own container; tests pass
    ``[]`` (or just omit the kwarg) since the unit tests don't have a
    real Docker daemon to inspect.
    """
    name = _container_name(hint_run_id)
    env = dict(env_passthrough)
    env["STATION_RUN_ID"] = hint_run_id
    if project_repo is not None:
        env["STATION_PROJECT_REPO"] = project_repo

    # Named volumes that hold workspace + log state shared across all
    # runs. Both must be explicitly named in compose.yml (``name:`` key)
    # so compose doesn't project-prefix them, or these mounts will
    # silently land on freshly-created EMPTY volumes — see compose.yml
    # for the relevant override.
    volumes: dict[str, dict] = {
        "station-data": {"bind": "/var/lib/claude-agent-station", "mode": "rw"},
        "station-logs": {"bind": "/var/log/claude-agent", "mode": "rw"},
    }
    # Layer the inherited bind mounts on top. We intentionally re-bind
    # whatever destination the launcher had — same host path, same mode
    # — because the runner needs an identical view of the Claude/gh
    # credential dirs to authenticate.
    for mount in inherited_mounts or ():
        volumes[mount["source"]] = {
            "bind": mount["destination"],
            "mode": mount.get("mode", "rw"),
        }

    container = client.containers.run(
        image=image,
        name=name,
        detach=True,
        remove=True,            # auto-cleanup on exit
        init=True,              # proper PID 1 zombie reaping inside the runner
        network="agent-net",
        mem_limit=quotas["memory"],
        nano_cpus=_cpus_to_nano(quotas["cpus"]),
        environment=env,
        volumes=volumes,
        command=[
            "python", "-m", "agent.station_orchestrator",
            "--driver",
            "--run-id", hint_run_id,
            "--config", config_path,
            "--workspaces-dir", workspaces_dir,
        ],
    )
    now = datetime.now(timezone.utc)
    return RunnerHandle(
        run_id=hint_run_id,
        container_name=container.name,
        started_at=now,
        last_webhook_at=now,
        project_repo=project_repo,
    )
