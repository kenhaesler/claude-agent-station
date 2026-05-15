"""Launcher runs map (#386)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import launcher


def test_runners_dict_starts_empty():
    launcher._runners.clear()
    assert launcher._runners == {}


def test_runner_handle_stores_metadata():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-abc",
        container_name="cas-runner-abc",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo="x/y",
    )
    launcher._runners[handle.run_id] = handle
    assert launcher._runners["run-abc"].container_name == "cas-runner-abc"
    assert launcher._runners["run-abc"].project_repo == "x/y"


from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


def test_status_returns_runs_list():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-s1",
        container_name="cas-runner-s1",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo="x/y",
    )
    launcher._runners[handle.run_id] = handle

    resp = TestClient(launcher.app).get("/status")
    body = resp.json()
    assert "runs" in body
    assert any(r["run_id"] == "run-s1" for r in body["runs"])


def test_stop_endpoint_calls_docker_stop():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-s2",
        container_name="cas-runner-s2",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo=None,
    )
    launcher._runners[handle.run_id] = handle

    with patch("agent.launcher._get_docker_client") as get_client:
        fake_client = MagicMock()
        get_client.return_value = fake_client
        resp = TestClient(launcher.app).post("/stop", params={"run_id": "run-s2"})

    assert resp.status_code == 200
    fake_client.containers.get.assert_called_with("cas-runner-s2")


def test_webhook_tick_bumps_last_webhook_at():
    launcher._runners.clear()
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    handle = launcher.RunnerHandle(
        run_id="run-s3",
        container_name="cas-runner-s3",
        started_at=earlier,
        last_webhook_at=earlier,
        project_repo=None,
    )
    launcher._runners[handle.run_id] = handle

    resp = TestClient(launcher.app).post("/webhook-tick", params={"run_id": "run-s3"})
    assert resp.status_code == 200
    assert launcher._runners["run-s3"].last_webhook_at > earlier
