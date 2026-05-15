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


def test_spawn_runner_layers_inherited_mounts():
    """Inherited mounts (Claude creds, gh auth, db_password) are added
    on top of the named-volume mounts. Without them the runner can't
    authenticate the Claude CLI or open a DB connection.
    """
    from agent.runner_spawn import spawn_runner

    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "cas-runner-mnt"
    fake_client.containers.run.return_value = fake_container

    inherited = [
        {"source": "/home/op/.claude", "destination": "/root/.claude", "mode": "rw"},
        {"source": "/home/op/.config/gh", "destination": "/root/.config/gh", "mode": "ro"},
        {"source": "/host/secrets/db_password",
         "destination": "/run/secrets/db_password", "mode": "ro"},
    ]
    spawn_runner(
        fake_client,
        hint_run_id="run-mnt",
        project_repo=None,
        quotas=_quotas(),
        env_passthrough={},
        image="claude-agent-station/agent:dev",
        config_path="/cfg.json",
        workspaces_dir="/ws",
        inherited_mounts=inherited,
    )
    volumes = fake_client.containers.run.call_args.kwargs["volumes"]
    # Named volumes still present.
    assert volumes["station-data"]["bind"] == "/var/lib/claude-agent-station"
    assert volumes["station-logs"]["bind"] == "/var/log/claude-agent"
    # Inherited binds added.
    assert volumes["/home/op/.claude"]["bind"] == "/root/.claude"
    assert volumes["/home/op/.claude"]["mode"] == "rw"
    assert volumes["/home/op/.config/gh"]["mode"] == "ro"
    assert volumes["/host/secrets/db_password"]["bind"] == "/run/secrets/db_password"


def test_spawn_runner_accepts_none_inherited_mounts():
    """``inherited_mounts=None`` (the default) is treated as 'no extras'.

    The launcher passes ``[]`` when running outside Docker — runner_spawn
    must accept both ``None`` and ``[]`` without raising.
    """
    from agent.runner_spawn import spawn_runner

    fake_client = MagicMock()
    fake_client.containers.run.return_value.name = "cas-runner-none"
    spawn_runner(
        fake_client,
        hint_run_id="run-none",
        project_repo=None,
        quotas=_quotas(),
        env_passthrough={},
        image="x:latest",
        config_path="/c",
        workspaces_dir="/w",
        inherited_mounts=None,
    )
    volumes = fake_client.containers.run.call_args.kwargs["volumes"]
    # Only the named-volume mounts.
    assert set(volumes.keys()) == {"station-data", "station-logs"}


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


def test_env_passthrough_whitelist_runner_needs(monkeypatch):
    """The runner is the orchestrator process — it MUST receive the
    auth secrets it uses to call back to the dashboard. The earlier
    too-strict whitelist (PR #419 review) blocked the webhook secret
    and the runner 401'd on every event, aborting in preflight.

    What we still refuse to copy:
    - ``STATION_LAUNCHER_TOKEN`` (dashboard → launcher hop only)
    - Everything outside the whitelist
    """
    monkeypatch.setenv("STATION_API_KEY", "secret-api")
    monkeypatch.setenv("STATION_WEBHOOK_SECRET", "secret-webhook")
    monkeypatch.setenv("STATION_GITHUB_WEBHOOK_SECRET", "secret-gh-wh")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-launcher-token")
    monkeypatch.setenv("STATION_DB_URL", "postgresql+asyncpg://u:p@db/h")
    monkeypatch.setenv("STATION_CONFIG", "/tmp/cfg.json")
    monkeypatch.setenv("IS_SANDBOX", "1")
    monkeypatch.setenv("PATH", "/should/not/leak")
    monkeypatch.setenv("HOME", "/should/not/leak")

    import importlib
    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    env = launcher_mod._env_passthrough()
    # Required for runner → dashboard auth.
    assert env["STATION_API_KEY"] == "secret-api"
    assert env["STATION_WEBHOOK_SECRET"] == "secret-webhook"
    assert env["STATION_GITHUB_WEBHOOK_SECRET"] == "secret-gh-wh"
    # Required for Claude CLI under root.
    assert env["IS_SANDBOX"] == "1"
    # Whitelisted entries must flow through unchanged.
    assert env["STATION_DB_URL"].startswith("postgresql+asyncpg://")
    assert env["STATION_CONFIG"] == "/tmp/cfg.json"
    # Launcher token still excluded — runner has no reason to call the
    # launcher's own endpoints.
    assert "STATION_LAUNCHER_TOKEN" not in env
    # Random non-whitelisted env vars don't leak.
    assert "PATH" not in env
    assert "HOME" not in env


def test_spawn_runner_container_injects_gh_token(monkeypatch):
    """The runner needs ``GH_TOKEN`` so ``git clone`` can use the
    GitHub App installation token via the embedded-URL credential path
    (see ``workspace_setup._clone_url``). The legacy inline path
    (``_spawn_run_manager``) already fetches the token; PR #386's
    runner port forgot to carry it over and the first live triggered
    run aborted at clone time.
    """
    monkeypatch.setenv("STATION_RUNNER_MODE", "container")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    import importlib
    from fastapi.testclient import TestClient
    from agent.runner_spawn import RunnerHandle
    from datetime import datetime, timezone
    import agent.launcher as launcher
    importlib.reload(launcher)  # pick up the empty LAUNCHER_TOKEN

    with patch("agent.launcher._fetch_gh_token", return_value="ghs_app_token") as fetch, \
         patch("agent.launcher._get_docker_client") as get_client, \
         patch("agent.launcher._get_inherited_mounts", return_value=[]), \
         patch("agent.launcher.spawn_runner") as spawn:
        spawn.return_value = RunnerHandle(
            run_id="run-tok", container_name="cas-runner-tok",
            started_at=datetime.now(timezone.utc),
            last_webhook_at=datetime.now(timezone.utc),
            project_repo=None,
        )
        get_client.return_value = MagicMock()
        resp = TestClient(launcher.app).post("/run", json={"hint_run_id": "run-tok"})

    assert resp.status_code == 200
    fetch.assert_called_once()
    env = spawn.call_args.kwargs["env_passthrough"]
    assert env["GH_TOKEN"] == "ghs_app_token"


def test_spawn_runner_container_tolerates_missing_gh_token(monkeypatch):
    """When the dashboard's GitHub App isn't installed (or unreachable),
    ``_fetch_gh_token`` returns ``None``. The runner spawn must still
    proceed — git clone will fail with a clear stdin-auth error rather
    than this code path raising.
    """
    monkeypatch.setenv("STATION_RUNNER_MODE", "container")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    import importlib
    from fastapi.testclient import TestClient
    from agent.runner_spawn import RunnerHandle
    from datetime import datetime, timezone
    import agent.launcher as launcher
    importlib.reload(launcher)

    with patch("agent.launcher._fetch_gh_token", return_value=None), \
         patch("agent.launcher._get_docker_client") as get_client, \
         patch("agent.launcher._get_inherited_mounts", return_value=[]), \
         patch("agent.launcher.spawn_runner") as spawn:
        spawn.return_value = RunnerHandle(
            run_id="run-no-tok", container_name="cas-runner-no-tok",
            started_at=datetime.now(timezone.utc),
            last_webhook_at=datetime.now(timezone.utc),
            project_repo=None,
        )
        get_client.return_value = MagicMock()
        resp = TestClient(launcher.app).post("/run", json={"hint_run_id": "run-no-tok"})

    assert resp.status_code == 200
    env = spawn.call_args.kwargs["env_passthrough"]
    assert "GH_TOKEN" not in env


def test_get_inherited_mounts_returns_empty_outside_docker(monkeypatch):
    """Without ``/etc/hostname`` resolving to a real container, the
    helper degrades to an empty list rather than raising. Unit tests
    rely on this — they import the module without a Docker daemon
    handy.
    """
    import importlib
    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    # Force the "no container id" fallback by patching the file reader.
    monkeypatch.setattr(launcher_mod, "_read_own_container_id", lambda: None)
    # Reset the cache so we get a fresh probe.
    launcher_mod._inherited_mounts = None

    assert launcher_mod._get_inherited_mounts() == []


def test_get_inherited_mounts_filters_to_known_destinations(monkeypatch):
    """The helper only forwards the operator-level mounts the runner
    needs (Claude creds, gh auth, db_password secret). Random binds on
    the launcher (e.g. ``/var/log/journal``) are NOT inherited.
    """
    import importlib
    from unittest.mock import MagicMock
    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    monkeypatch.setattr(launcher_mod, "_read_own_container_id", lambda: "deadbeef")

    fake_container = MagicMock()
    fake_container.attrs = {
        "Mounts": [
            {"Type": "bind", "Source": "/home/op/.claude",
             "Destination": "/root/.claude", "RW": True},
            {"Type": "bind", "Source": "/home/op/.config/gh",
             "Destination": "/root/.config/gh", "RW": False},
            {"Type": "bind", "Source": "/host/secrets/db_password",
             "Destination": "/run/secrets/db_password", "RW": False},
            {"Type": "bind", "Source": "/var/log/journal",
             "Destination": "/var/log/journal", "RW": True},  # not inherited
        ],
    }
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container
    monkeypatch.setattr(launcher_mod, "_get_docker_client", lambda: fake_client)
    launcher_mod._inherited_mounts = None

    mounts = launcher_mod._get_inherited_mounts()
    destinations = {m["destination"] for m in mounts}
    assert destinations == {
        "/root/.claude",
        "/root/.config/gh",
        "/run/secrets/db_password",
    }
    # ``ro`` flag preserved for read-only mounts.
    by_dst = {m["destination"]: m for m in mounts}
    assert by_dst["/root/.claude"]["mode"] == "rw"
    assert by_dst["/root/.config/gh"]["mode"] == "ro"
    assert by_dst["/run/secrets/db_password"]["mode"] == "ro"
