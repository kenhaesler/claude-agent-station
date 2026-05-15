"""Docker SDK spawn invocation (#386)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _quotas(memory: str = "2g", cpus: str = "1.0") -> dict:
    return {"memory": memory, "cpus": cpus}


def test_spawn_runner_passes_expected_args(monkeypatch):
    from agent.runner_spawn import spawn_runner

    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "cas-runner-abc"
    fake_client.containers.run.return_value = fake_container

    handle = spawn_runner(
        fake_client,
        hint_run_id="run-abc",
        project_repo="x/y",
        quotas=_quotas("1g", "0.5"),
        env_passthrough={"STATION_DB_URL": "postgresql+asyncpg://u:p@db/h",
                         "STATION_WEBHOOK_URL": "http://dashboard:8420/api/webhook/run-event"},
        image="claude-agent-station/agent:dev",
        config_path="/var/lib/claude-agent-station/manager-config.json",
        workspaces_dir="/var/lib/claude-agent-station/workspaces",
    )

    fake_client.containers.run.assert_called_once()
    kwargs = fake_client.containers.run.call_args.kwargs
    assert kwargs["image"] == "claude-agent-station/agent:dev"
    assert kwargs["name"] == "cas-runner-abc"
    assert kwargs["detach"] is True
    assert kwargs["remove"] is True
    assert kwargs["init"] is True
    assert kwargs["mem_limit"] == "1g"
    # docker SDK uses nano-cpus (int) — 0.5 cpu = 500_000_000 nanos.
    assert kwargs["nano_cpus"] == 500_000_000
    env = kwargs["environment"]
    assert env["STATION_RUN_ID"] == "run-abc"
    assert env["STATION_PROJECT_REPO"] == "x/y"
    assert env["STATION_DB_URL"].startswith("postgresql+asyncpg://")
    assert env["STATION_WEBHOOK_URL"].endswith("/api/webhook/run-event")
    cmd = kwargs["command"]
    assert "agent.station_orchestrator" in cmd
    assert "--driver" in cmd
    assert "--run-id" in cmd and "run-abc" in cmd

    volumes = kwargs["volumes"]
    assert "station-data" in volumes
    assert volumes["station-data"]["bind"] == "/var/lib/claude-agent-station"
    assert "station-logs" in volumes

    assert handle.run_id == "run-abc"
    assert handle.container_name == "cas-runner-abc"
    assert handle.project_repo == "x/y"


from unittest.mock import patch


def test_post_run_invokes_spawn_runner_when_mode_container(monkeypatch):
    monkeypatch.setenv("STATION_RUNNER_MODE", "container")
    from fastapi.testclient import TestClient
    from agent import launcher

    with patch("agent.launcher._get_docker_client") as get_client, \
         patch("agent.launcher.spawn_runner") as spawn:
        fake_client = MagicMock()
        get_client.return_value = fake_client
        from agent.runner_spawn import RunnerHandle
        from datetime import datetime, timezone
        spawn.return_value = RunnerHandle(
            run_id="run-x",
            container_name="cas-runner-x",
            started_at=datetime.now(timezone.utc),
            last_webhook_at=datetime.now(timezone.utc),
            project_repo="x/y",
        )

        client_app = TestClient(launcher.app)
        resp = client_app.post("/run", json={"hint_run_id": "run-x"})

    assert resp.status_code == 200
    spawn.assert_called_once()
    assert "cas-runner-x" in resp.json()["container"]


def test_post_run_falls_back_to_inline_when_mode_inline(monkeypatch):
    monkeypatch.setenv("STATION_RUNNER_MODE", "inline")
    from fastapi.testclient import TestClient
    from agent import launcher

    with patch("agent.launcher._spawn_run_manager") as legacy:
        legacy.return_value = {"status": "triggered", "pid": 4242, "log": "/dev/null"}
        client_app = TestClient(launcher.app)
        resp = client_app.post("/run", json={"hint_run_id": "run-y"})
    assert resp.status_code == 200
    assert resp.json()["pid"] == 4242
