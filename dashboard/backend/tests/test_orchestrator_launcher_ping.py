"""Tests for the launcher heartbeat ping fired by station_orchestrator
.post_webhook. Bug: PR #364's launcher zombie reaper killed actively-
working runs because its heartbeat signal was bumped only from the
bash-side webhook_event wrapper, but Agent Teams runs emit nearly all
their webhook traffic from the Python orchestrator path. See
post-mortem of run-20260512T122255Z."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def test_post_webhook_pings_launcher_when_url_set(monkeypatch):
    """Every post_webhook call must also bump the launcher's heartbeat
    clock so its zombie reaper sees the orchestrator's progress."""
    from agent import station_orchestrator as so

    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "test-token")

    # Mock httpx.Client used both for the dashboard post and the
    # launcher ping. Two separate `with` blocks construct two clients;
    # both must receive a POST.
    dashboard_call = None
    launcher_call = None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            FakeClient.last_calls.append((url, kwargs))
    FakeClient.last_calls = []

    with patch("agent.station_orchestrator.httpx.Client", FakeClient):
        so.post_webhook(
            {"dashboard": {"webhook_url": "http://dashboard:8420/api/webhook/run-event"}},
            "narration",
            {"run_id": "run-test"},
        )

    urls = [c[0] for c in FakeClient.last_calls]
    assert any("/api/webhook/run-event" in u for u in urls), urls
    assert any("/webhook-tick" in u for u in urls), urls

    # Verify the token was passed on the launcher ping
    for url, kwargs in FakeClient.last_calls:
        if "/webhook-tick" in url:
            assert kwargs.get("headers", {}).get("X-Launcher-Token") == "test-token"


def test_post_webhook_pings_default_localhost_when_url_unset(monkeypatch):
    """When STATION_AGENT_LAUNCHER_URL is unset, the ping must still
    fire against the in-container default (http://localhost:8421).
    The original 'skip' design hid a real bug: in the compose deployment
    the env var was missing on the agent side, so every Agent Teams run
    silently failed to ping the launcher and got reaped after 120s."""
    from agent import station_orchestrator as so

    monkeypatch.delenv("STATION_AGENT_LAUNCHER_URL", raising=False)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, **kwargs):
            self.calls.append(url)
            FakeClient.last_calls.append(url)
    FakeClient.last_calls = []

    with patch("agent.station_orchestrator.httpx.Client", FakeClient):
        so.post_webhook(
            {"dashboard": {"webhook_url": "http://dashboard:8420/api/webhook/run-event"}},
            "narration",
            {"run_id": "run-test"},
        )

    ticks = [u for u in FakeClient.last_calls if "/webhook-tick" in u]
    assert len(ticks) == 1, f"expected one /webhook-tick call, saw {FakeClient.last_calls}"
    assert "localhost:8421" in ticks[0], f"expected localhost default, got: {ticks[0]}"


def test_launcher_ping_swallows_errors(monkeypatch):
    """A launcher that's down must not break the dashboard webhook —
    the ping is best-effort by contract."""
    from agent import station_orchestrator as so

    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://broken-host:9999")

    import httpx
    class FlakyClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, **kwargs):
            # First call (dashboard webhook): success.
            # Second call (launcher ping): raise.
            if "/webhook-tick" in url:
                raise httpx.ConnectError("simulated launcher down")
            return MagicMock(status_code=200)

    with patch("agent.station_orchestrator.httpx.Client", FlakyClient):
        # Must not raise even though the launcher ping fails
        so.post_webhook(
            {"dashboard": {"webhook_url": "http://dashboard:8420/api/webhook/run-event"}},
            "run_start",
            {"run_id": "run-flaky"},
        )


def test_post_webhook_threads_run_id_to_launcher_tick(monkeypatch):
    """Container-mode runners MUST pass ``run_id`` to /webhook-tick so
    the launcher updates ``handle.last_webhook_at`` in its ``_runners``
    map. Without it, the launcher's handler falls through to the
    legacy global ``_last_webhook_at`` and the container reaper
    SIGTERMs the runner at the 120s idle mark even while
    ``post_webhook`` keeps firing. Regression guard for the second
    half of the #386 reaper bug (the first half — emit() path — was
    fixed in PR #431). Discovered when live run-20260515T233935Z
    still died at 134s after PR #431: the first tick had ``?run_id=``
    but every subsequent ``post_webhook`` ping (the dominant traffic)
    did not.
    """
    from agent import station_orchestrator as so

    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")

    captured: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, **kwargs):
            captured.append((url, kwargs))

    with patch("agent.station_orchestrator.httpx.Client", FakeClient):
        so.post_webhook(
            {"dashboard": {"webhook_url": "http://dashboard:8420/api/webhook/run-event"}},
            "teammate_progress",
            {"run_id": "run-tick-id", "task_id": "t1"},
        )

    tick_calls = [(u, kw) for (u, kw) in captured if "/webhook-tick" in u]
    assert len(tick_calls) == 1, f"expected one tick, saw {captured}"
    _, kwargs = tick_calls[0]
    assert kwargs.get("params") == {"run_id": "run-tick-id"}, (
        f"params={{run_id=...}} must be on the tick POST; got {kwargs}"
    )


def test_post_webhook_omits_run_id_param_when_data_missing(monkeypatch):
    """If a caller invokes ``post_webhook`` without a ``run_id`` in
    ``data`` (or with ``data=None``), the launcher tick must send
    empty params, NOT ``{"run_id": "None"}`` or similar. The
    launcher's handler interprets the absence of the param as legacy
    inline-mode and bumps the global timestamp instead.
    """
    from agent import station_orchestrator as so

    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")

    captured: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, **kwargs):
            captured.append((url, kwargs))

    with patch("agent.station_orchestrator.httpx.Client", FakeClient):
        so.post_webhook(
            {"dashboard": {"webhook_url": "http://dashboard:8420/api/webhook/run-event"}},
            "narration",
            None,
        )

    tick_calls = [(u, kw) for (u, kw) in captured if "/webhook-tick" in u]
    assert len(tick_calls) == 1
    _, kwargs = tick_calls[0]
    assert kwargs.get("params") == {}, (
        f"empty params expected when run_id is missing; got {kwargs}"
    )
