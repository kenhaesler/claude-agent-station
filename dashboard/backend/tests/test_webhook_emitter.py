"""Tests for agent/webhook_emitter.py (issue #349, sub-PR 5a)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def test_emit_run_start_posts_to_webhook():
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-test-1", payload={"project": "x/y"})
        assert mock_post.called
        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
        assert "/api/webhook/run-event" in url
        assert body["event"] == "run_start"
        assert body["run_id"] == "run-test-1"
        assert body["project"] == "x/y"


def test_emit_retries_on_5xx():
    """3 attempts with exponential backoff; eventual success returns cleanly."""
    from agent.webhook_emitter import emit
    responses = [
        MagicMock(status_code=500, text="boom"),
        MagicMock(status_code=500, text="boom"),
        MagicMock(status_code=200, text="ok"),
    ]
    with patch("agent.webhook_emitter.httpx.post", side_effect=responses), \
         patch("agent.webhook_emitter.time.sleep") as mock_sleep:
        emit("run_complete", run_id="run-test-2",
             payload={"status": "completed"})
        # Should have slept twice (0.5s, 1s) between the three attempts
        assert mock_sleep.call_count == 2


def test_emit_does_not_raise_on_final_failure():
    """Orchestrator must never be killed by a dashboard outage."""
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post",
               side_effect=[MagicMock(status_code=500, text="boom")] * 3), \
         patch("agent.webhook_emitter.time.sleep"):
        # No exception
        emit("run_complete", run_id="run-test-3",
             payload={"status": "completed"})


def test_emit_does_not_retry_on_4xx():
    """4xx is a client error; retrying won't help."""
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post",
               side_effect=[MagicMock(status_code=400, text="bad")]) as mock_post:
        emit("run_complete", run_id="run-test-4",
             payload={"status": "completed"})
        assert mock_post.call_count == 1
