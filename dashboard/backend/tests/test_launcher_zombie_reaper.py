"""Tests for agent/launcher.py zombie reaper (#360 option 1)."""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def launcher_module():
    """Reload agent.launcher to get a fresh module-level state for each test."""
    # Ensure the agent package is importable from the repo root.  The
    # conftest.py already adds the repo root to sys.path; if it doesn't
    # have the agent directory, skip gracefully.
    import agent.launcher as launcher
    importlib.reload(launcher)
    yield launcher


def test_webhook_tick_bumps_timestamp(launcher_module):
    """POST /webhook-tick must bump _last_webhook_at when a run is active."""
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    fake_proc.pid = 999
    launcher_module._current = fake_proc

    from fastapi.testclient import TestClient
    client = TestClient(launcher_module.app)
    resp = client.post("/webhook-tick")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "stale" not in body or body["stale"] is not True
    assert launcher_module._last_webhook_at is not None


def test_webhook_tick_when_no_run(launcher_module):
    """POST /webhook-tick returns stale=True when no run is active.
    Doesn't error — a slow webhook from a just-finished run is normal."""
    launcher_module._current = None

    from fastapi.testclient import TestClient
    client = TestClient(launcher_module.app)
    resp = client.post("/webhook-tick")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body.get("stale") is True


def test_reaper_terminates_silent_subprocess(launcher_module):
    """The reaper's core logic: alive subprocess with stale heartbeat
    gets SIGTERM and state is cleared."""
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    fake_proc.pid = 12
    launcher_module._current = fake_proc

    # Heartbeat older than the timeout
    too_old = datetime.now(timezone.utc) - timedelta(
        seconds=launcher_module.ZOMBIE_TIMEOUT_SECONDS + 30,
    )
    launcher_module._last_webhook_at = too_old

    launcher_module._reap_once()

    fake_proc.terminate.assert_called_once()
    assert launcher_module._current is None
    assert launcher_module._last_webhook_at is None


def test_reaper_skips_recent_heartbeat(launcher_module):
    """A subprocess with a fresh heartbeat is NOT terminated."""
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    fake_proc.pid = 13
    launcher_module._current = fake_proc
    launcher_module._last_webhook_at = datetime.now(timezone.utc)

    launcher_module._reap_once()

    fake_proc.terminate.assert_not_called()
    # Still tracked — not cleared
    assert launcher_module._current is fake_proc
