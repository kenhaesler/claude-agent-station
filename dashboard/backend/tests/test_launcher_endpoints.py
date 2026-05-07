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
    assert resp.json() == {"running": False, "pid": None, "exit_code": None}


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


def test_run_passes_gh_token_via_env_when_dashboard_provides_one(monkeypatch, tmp_path):
    """The launcher fetches a fresh installation token from the dashboard
    before spawning run-manager.sh and exports it as GH_TOKEN."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    # Provide a fake script that just records its env to a file
    sentinel = tmp_path / "env.out"
    fake_script = tmp_path / "fake-run-manager.sh"
    fake_script.write_text(f"""#!/bin/bash
env | grep '^GH_TOKEN=' > {sentinel}
""")
    fake_script.chmod(0o755)
    monkeypatch.setenv("STATION_RUN_MANAGER", str(fake_script))
    monkeypatch.setenv("STATION_DASHBOARD_BASE_URL", "http://test")
    monkeypatch.setenv("STATION_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("STATION_WORKDIR", str(tmp_path))

    # Mock the dashboard's /api/github/app/token to return a fixed token
    import respx
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    from fastapi.testclient import TestClient
    client = TestClient(launcher_mod.app)

    with respx.mock() as mock:
        mock.get("http://test/api/github/app/token").respond(
            200, json={"token": "ghs_test_inject"},
        )
        resp = client.post("/run")

    assert resp.status_code == 200
    # Wait briefly for the spawned subprocess to exit and write the env file
    import time
    for _ in range(20):
        if sentinel.exists():
            break
        time.sleep(0.1)

    assert sentinel.exists(), "spawned script never ran"
    assert "GH_TOKEN=ghs_test_inject" in sentinel.read_text()
