"""Tests for the orchestrator_complete work-completion gate (run-20260512T205423Z).

After the _user_prompt_stream stdin fix (PR #381), the SDK keeps stdin
open for the full lifetime of the lead session — so the lead can emit
multiple ResultMessages during one query() call (e.g., turn-complete
when delegation finishes, then later a real completion).

handle_stream_event's session_id gate (added in #371) prevents
teammate sub-session ResultMessages from firing orchestrator_complete,
but it doesn't gate intermediate lead ResultMessages. Without an
extra check, the FIRST lead ResultMessage — emitted seconds after
delegation, while teammates are still running — marks the run
terminal in the dashboard.

These tests pin the contract: orchestrator_complete fires ONCE, on
the ResultMessage whose result text passes _is_work_complete().
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


_MAIN_SESSION = "session-main-abc"
_SUB_SESSION = "session-teammate-xyz"


def _result_message(result_text: str, *, session_id: str = _MAIN_SESSION,
                    num_turns: int = 1):
    """Build a minimal ResultMessage-shaped object handle_stream_event accepts."""
    from claude_agent_sdk.types import ResultMessage

    # Construct via SimpleNamespace then promote isinstance via a real class
    # would be cleaner, but handle_stream_event only uses isinstance() and
    # attribute access; we use the real type so isinstance(ResultMessage)
    # passes inside handle_stream_event.
    msg = ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=500,
        is_error=False,
        num_turns=num_turns,
        session_id=session_id,
        total_cost_usd=0.0,
        usage=None,
        result=result_text,
    )
    return msg


def _state():
    """Build a _StreamState with main_session_id captured."""
    from agent.station_orchestrator import _StreamState

    s = _StreamState(last_webhook_time=0.0)
    s.main_session_id = _MAIN_SESSION
    s.tokens_in = 1000
    s.tokens_out = 500
    s.turns = 3
    return s


def _collect_emits(handler_fn, message):
    """Drive handle_stream_event under a patched post_webhook and return
    the list of (event_name, payload) emitted.
    """
    from agent.station_orchestrator import handle_stream_event
    seen: list[tuple[str, dict]] = []
    with patch("agent.station_orchestrator.post_webhook",
               side_effect=lambda cfg, event, payload: seen.append((event, payload))):
        handle_stream_event(message, config={}, run_id="testrun",
                            log_file=None, state=_state())
    return seen


# ── The new gate ──────────────────────────────────────────────────────


def test_intermediate_lead_result_does_not_emit_orchestrator_complete():
    """A ResultMessage from the lead's main session with prose that doesn't
    match _is_work_complete (no 'final summary', no 'issues_completed', no
    'all teammates completed') MUST NOT emit orchestrator_complete. This
    is the bug from run-20260512T205423Z: the lead's delegation-turn
    ResultMessage fired orchestrator_complete at 21:05:29 while teammates
    were still working until 21:17.
    """
    from agent.station_orchestrator import handle_stream_event

    msg = _result_message(
        "I've delegated the work to the team. They will report back.",
    )
    seen = _collect_emits(handle_stream_event, msg)

    events = [e for e, _ in seen]
    assert "orchestrator_complete" not in events, (
        f"orchestrator_complete fired on intermediate ResultMessage — "
        f"emitted: {events}"
    )
    # progress_update IS allowed (token totals get flushed) — that's a
    # current-state event, not a lifecycle terminal.
    assert "progress_update" in events


def test_completion_lead_result_with_final_summary_emits_orchestrator_complete():
    """The terminal ResultMessage (matching _is_work_complete) MUST fire
    orchestrator_complete. Pins that the fix didn't over-correct.
    """
    msg = _result_message(
        "Final summary: all three teammates pushed branches and the manager "
        "reviewed each. PR verdicts opened for human review.",
        num_turns=42,
    )
    seen = _collect_emits(None, msg)

    events = [e for e, _ in seen]
    assert "orchestrator_complete" in events, (
        f"orchestrator_complete should fire on terminal ResultMessage — "
        f"emitted: {events}"
    )
    # The payload should carry the actual turn count for telemetry.
    complete_payload = next(p for e, p in seen if e == "orchestrator_complete")
    assert complete_payload["num_turns"] == 42
    assert complete_payload["is_error"] is False


def test_completion_lead_result_with_structured_json_summary_emits():
    """_is_work_complete also matches the structured JSON form
    ('issues_completed' + 'issues_failed' keys). Cover it too.
    """
    msg = _result_message(
        '```json\n{"issues_completed": [27], "issues_failed": []}\n```',
    )
    seen = _collect_emits(None, msg)
    assert "orchestrator_complete" in [e for e, _ in seen]


def test_subsession_result_message_still_filtered():
    """The session_id gate from #371 must still apply: a ResultMessage with
    a session_id that doesn't match the captured main session must be
    skipped, regardless of result_text. Regression guard.
    """
    msg = _result_message(
        "Final summary: I did some work.",
        session_id=_SUB_SESSION,  # NOT the main session
    )
    seen = _collect_emits(None, msg)
    events = [e for e, _ in seen]
    # Neither orchestrator_complete nor progress_update should fire — the
    # whole branch returns early when the session doesn't match.
    assert "orchestrator_complete" not in events
    assert "progress_update" not in events
