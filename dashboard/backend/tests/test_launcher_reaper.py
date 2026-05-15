"""Container-aware reaper (#386)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent import launcher
from agent.launcher_reaper import reap_once


def _handle(run_id: str, age_s: int) -> launcher.RunnerHandle:
    return launcher.RunnerHandle(
        run_id=run_id,
        container_name=f"cas-runner-{run_id.removeprefix('run-')}",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        last_webhook_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        project_repo=None,
    )


def test_reaper_stops_silent_container():
    launcher._runners.clear()
    launcher._runners["run-stale"] = _handle("run-stale", 600)
    client = MagicMock()
    reap_once(client, zombie_timeout_seconds=120)
    client.containers.get.assert_called_with("cas-runner-stale")
    client.containers.get.return_value.stop.assert_called_with(timeout=30)
    assert "run-stale" not in launcher._runners


def test_reaper_leaves_active_container_alone():
    launcher._runners.clear()
    launcher._runners["run-fresh"] = _handle("run-fresh", 5)
    client = MagicMock()
    reap_once(client, zombie_timeout_seconds=120)
    client.containers.get.return_value.stop.assert_not_called()
    assert "run-fresh" in launcher._runners


def test_reaper_drops_missing_container():
    launcher._runners.clear()
    launcher._runners["run-gone"] = _handle("run-gone", 5)
    client = MagicMock()
    # docker SDK raises docker.errors.NotFound when get() can't find a container.
    import docker.errors as derr
    client.containers.get.side_effect = derr.NotFound("gone")
    reap_once(client, zombie_timeout_seconds=120)
    assert "run-gone" not in launcher._runners
