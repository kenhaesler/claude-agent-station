"""Unit tests for the agent launcher's HTTP endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    """Reload the launcher module so each test gets a fresh _current global
    and its own LAUNCHER_TOKEN reading."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    monkeypatch.setenv("STATION_RUN_MANAGER", "/nonexistent/run-manager.sh")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    return launcher_mod.app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_stop_when_no_run_in_flight_returns_409(client):
    resp = client.post("/stop")
    assert resp.status_code == 409
    assert "no run is currently running" in resp.json()["detail"].lower()


def test_stop_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-xyz")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    client = TestClient(launcher_mod.app)

    # Without token
    resp = client.post("/stop")
    assert resp.status_code == 401

    # With wrong token
    resp = client.post("/stop", headers={"X-Launcher-Token": "wrong"})
    assert resp.status_code == 401


def test_status_does_not_require_token(monkeypatch):
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-xyz")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    client = TestClient(launcher_mod.app)

    resp = client.get("/status")
    assert resp.status_code == 200
    # After #386 PR-2, /status returns a runs list keyed by run_id.
    assert "runs" in resp.json()


def test_stop_terminates_in_flight_run_and_returns_pid(monkeypatch):
    """When a run is in flight, /stop must call terminate() on the tracked
    subprocess and respond 200 with the pid."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    class _FakePopen:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.returncode: int | None = None
            self.terminated = False

        def poll(self) -> int | None:
            return self.returncode  # None == still running

        def terminate(self) -> None:
            self.terminated = True

    fake = _FakePopen(pid=4242)
    launcher_mod._current = fake

    client = TestClient(launcher_mod.app)
    resp = client.post("/stop")

    assert resp.status_code == 200
    assert resp.json() == {"status": "stopping", "pid": 4242}
    assert fake.terminated is True


def test_stop_auth_check_precedes_state_check(monkeypatch):
    """Even when a run is in flight, missing/wrong token returns 401, not 409
    or 200. Proves the auth gate fires first — important for the security
    claim that the endpoint can't be probed for run state without auth."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-xyz")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    class _FakePopen:
        pid = 1
        returncode: int | None = None
        terminated = False

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            type(self).terminated = True

    launcher_mod._current = _FakePopen()
    client = TestClient(launcher_mod.app)

    resp = client.post("/stop")  # no token
    assert resp.status_code == 401
    assert _FakePopen.terminated is False  # we did NOT reach terminate()



def test_run_default_spawns_python_driver_with_required_args(monkeypatch, tmp_path):
    """#361: by default the launcher spawns ``python3 -m agent.station_orchestrator
    --driver`` with --run-id / --config / --workspaces-dir set. We replace
    Popen with a recorder so the test doesn't actually fork python.

    Uses STATION_RUNNER_MODE=inline to exercise the legacy subprocess path;
    the Docker container path is covered by test_launcher_spawn.py (#386).
    """
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    monkeypatch.delenv("STATION_LAUNCHER_USE_BASH", raising=False)
    monkeypatch.setenv("STATION_DASHBOARD_BASE_URL", "http://test")
    monkeypatch.setenv("STATION_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("STATION_WORKDIR", str(tmp_path))
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("STATION_WORKSPACES", str(tmp_path / "ws"))
    # Force inline mode so this test hits the subprocess path, not Docker.
    monkeypatch.setenv("STATION_RUNNER_MODE", "inline")

    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    recorded: dict = {}

    class _FakeProc:
        pid = 9999
        def poll(self):
            return None  # pretend it's still running for /status

    def _record_popen(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["env"] = kwargs.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(launcher_mod.subprocess, "Popen", _record_popen)

    from fastapi.testclient import TestClient
    client = TestClient(launcher_mod.app)
    resp = client.post("/run", json={"hint_run_id": "run-test-driver"})

    assert resp.status_code == 200
    cmd = recorded["cmd"]
    # Python entry point, --driver flag present
    assert cmd[1:4] == ["-m", "agent.station_orchestrator", "--driver"]
    # --run-id propagates the hint verbatim (already prefixed)
    assert "--run-id" in cmd
    assert cmd[cmd.index("--run-id") + 1] == "run-test-driver"
    # --config / --workspaces-dir wired from env
    assert cmd[cmd.index("--config") + 1] == str(tmp_path / "cfg.json")
    assert cmd[cmd.index("--workspaces-dir") + 1] == str(tmp_path / "ws")
    # Webhook emitter knows how to ping the launcher
    assert "STATION_AGENT_LAUNCHER_URL" in recorded["env"]


