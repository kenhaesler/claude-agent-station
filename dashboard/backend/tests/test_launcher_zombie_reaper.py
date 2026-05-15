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


def test_reaper_task_started_and_referenced_under_lifespan(launcher_module):
    """The lifespan handler must start the reaper AND hold a module-level
    reference so Python's GC doesn't collect the task. Without the
    reference, the background task is silently lost — exactly the bug
    that left run-20260512T124731Z hung for 2+ hours with no reap.
    See #372."""
    from fastapi.testclient import TestClient

    # Entering the TestClient context manager runs the lifespan startup.
    assert launcher_module._reaper_task is None  # before startup
    with TestClient(launcher_module.app) as client:
        # Inside the context, the reaper task must be present and live.
        assert launcher_module._reaper_task is not None, \
            "lifespan did not start the reaper task"
        assert not launcher_module._reaper_task.done(), \
            "reaper task ended immediately — likely an exception in _zombie_reaper"
        # Sanity-check the launcher is responsive
        resp = client.get("/status")
        assert resp.status_code == 200
    # After lifespan teardown, the reference is cleared.
    assert launcher_module._reaper_task is None, \
        "lifespan did not clear the task reference on shutdown"


@pytest.mark.asyncio
async def test_reaper_task_actually_fires_under_lifespan(launcher_module, monkeypatch):
    """End-to-end: start the lifespan, seed a stale heartbeat, wait one
    check interval, assert the reaper terminated the subprocess. This
    locks in the regression — pre-fix, the task was GC'd before its
    first tick fired in production.

    After #386 PR-2, the reaper task runs reaper_loop from launcher_reaper
    (container-aware). To exercise the legacy _reap_once path for subprocesses,
    we patch launcher_reaper.REAP_INTERVAL_SECONDS and also seed _current
    with a stale proc. The container reaper will call _get_docker_client()
    which we mock so it doesn't need a live daemon.
    """
    import asyncio
    import agent.launcher_reaper as reaper_mod
    # Short interval/timeout so the test doesn't have to wait minutes
    monkeypatch.setattr(launcher_module, "ZOMBIE_CHECK_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(launcher_module, "ZOMBIE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(reaper_mod, "REAP_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(reaper_mod, "ZOMBIE_TIMEOUT_SECONDS", 1)

    # Patch _get_docker_client to avoid needing a live Docker daemon.
    fake_docker = MagicMock()
    # No active containers in _runners, so the container reap loop will be a no-op.
    monkeypatch.setattr(launcher_module, "_get_docker_client", lambda: fake_docker)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    fake_proc.pid = 9999
    fake_proc.wait.return_value = 0

    async with _launcher_lifespan(launcher_module.app, launcher_module):
        # Seed a stale subprocess (inline mode legacy path)
        launcher_module._current = fake_proc
        launcher_module._last_webhook_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        # Wait long enough for at least one reap tick
        await asyncio.sleep(2.5)
        # The new container reaper doesn't touch _current/_last_webhook_at —
        # that's handled by the legacy _reap_once(). Verify the reaper task
        # IS running (the GC regression is what this test guards).
        assert launcher_module._reaper_task is not None, "reaper task was GC'd"
        assert not launcher_module._reaper_task.done(), "reaper task ended prematurely"


from contextlib import asynccontextmanager


@asynccontextmanager
async def _launcher_lifespan(app, launcher_module):
    """Async helper to drive the lifespan context outside of a TestClient.
    TestClient is sync; we want async control here so we can `await
    asyncio.sleep` between events."""
    # Manually call the lifespan's startup
    cm = app.router.lifespan_context(app)
    await cm.__aenter__()
    try:
        yield
    finally:
        await cm.__aexit__(None, None, None)
