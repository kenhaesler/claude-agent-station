"""Tests for the session_id gate on orchestrator_complete emission.

PR #371 fixes the bug where every SDK ResultMessage — including those
from teammate sub-sessions spawned via the Agent tool — triggered an
orchestrator_complete webhook. That marked the parent run terminal
prematurely. See post-mortem of run-20260512T124731Z."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _make_result_msg(session_id: str, num_turns: int = 31, subtype: str = "error_max_turns"):
    """Build a stand-in for the SDK's ResultMessage that's identifiable
    via isinstance via the real class."""
    from claude_agent_sdk import ResultMessage
    m = MagicMock(spec=ResultMessage)
    m.session_id = session_id
    m.num_turns = num_turns
    m.subtype = subtype
    m.is_error = subtype.startswith("error")
    m.duration_ms = 100
    m.result = ""
    return m


def test_emits_orchestrator_complete_for_main_session():
    """When a ResultMessage's session_id matches the captured main
    session, orchestrator_complete must fire."""
    from agent.station_orchestrator import handle_stream_event, _StreamState
    state = _StreamState(main_session_id="lead-session-abc")
    msg = _make_result_msg("lead-session-abc", num_turns=120, subtype="success")

    with patch("agent.station_orchestrator.post_webhook") as mock_post:
        handle_stream_event(msg, config={}, run_id="r-1", state=state)

    events = [c.args[1] for c in mock_post.call_args_list]
    assert "orchestrator_complete" in events, f"main-session result must emit, got: {events}"


def test_skips_orchestrator_complete_for_teammate_session():
    """A ResultMessage from a teammate sub-session (different session_id)
    must NOT trigger orchestrator_complete. This is THE bug from
    run-20260512T124731Z."""
    from agent.station_orchestrator import handle_stream_event, _StreamState
    state = _StreamState(main_session_id="lead-session-abc")
    msg = _make_result_msg("qa-teammate-session-xyz", num_turns=31, subtype="error_max_turns")

    with patch("agent.station_orchestrator.post_webhook") as mock_post:
        handle_stream_event(msg, config={}, run_id="r-1", state=state)

    events = [c.args[1] for c in mock_post.call_args_list]
    assert "orchestrator_complete" not in events, \
        f"teammate sub-session result must NOT emit, got: {events}"


def test_skips_when_main_session_id_unknown():
    """Defensive: if we never captured the main session_id (e.g. SDK
    behavior changed and no init SystemMessage arrived), skip the emit
    rather than fire on every ResultMessage. The orchestrator's
    try/finally already handles graceful shutdown."""
    from agent.station_orchestrator import handle_stream_event, _StreamState
    state = _StreamState(main_session_id=None)
    msg = _make_result_msg("some-session", num_turns=5)

    with patch("agent.station_orchestrator.post_webhook") as mock_post:
        handle_stream_event(msg, config={}, run_id="r-1", state=state)

    events = [c.args[1] for c in mock_post.call_args_list]
    assert "orchestrator_complete" not in events, \
        f"missing main_session_id must skip emit, got: {events}"


def test_skips_when_message_lacks_session_id():
    """Some SDK message variants may not carry session_id. Skip emit
    rather than fire on every untraceable ResultMessage."""
    from agent.station_orchestrator import handle_stream_event, _StreamState
    state = _StreamState(main_session_id="lead-session-abc")
    msg = _make_result_msg("", num_turns=5)
    msg.session_id = None  # explicitly null out

    with patch("agent.station_orchestrator.post_webhook") as mock_post:
        handle_stream_event(msg, config={}, run_id="r-1", state=state)

    events = [c.args[1] for c in mock_post.call_args_list]
    assert "orchestrator_complete" not in events
