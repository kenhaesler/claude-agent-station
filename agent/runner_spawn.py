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
) -> RunnerHandle:
    """Spawn a detached, auto-removed runner container.

    ``client`` is a ``docker.from_env()`` instance (or a MagicMock in tests).
    ``quotas`` is ``{"memory": "2g", "cpus": "1.0"}`` — strings come from the
    Project row's columns; defaults are resolved upstream.
    ``env_passthrough`` carries every STATION_* the orchestrator needs.
    """
    name = _container_name(hint_run_id)
    env = dict(env_passthrough)
    env["STATION_RUN_ID"] = hint_run_id
    if project_repo is not None:
        env["STATION_PROJECT_REPO"] = project_repo

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
        volumes={
            "station-data": {
                "bind": "/var/lib/claude-agent-station",
                "mode": "rw",
            },
            "station-logs": {
                "bind": "/var/log/claude-agent",
                "mode": "rw",
            },
        },
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
