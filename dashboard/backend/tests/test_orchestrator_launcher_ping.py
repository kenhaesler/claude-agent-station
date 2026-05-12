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
