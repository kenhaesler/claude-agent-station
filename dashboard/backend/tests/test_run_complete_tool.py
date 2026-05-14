"""Tests for agent.tools.run_complete (issue #385)."""
from __future__ import annotations

import asyncio
import pytest


def test_run_complete_input_validates_happy():
    from agent.tools.run_complete import RunCompleteInput

    payload = {
        "status": "success",
        "verdicts": [
            {"project": "owner/repo", "issue_number": 1, "decision": "APPROVE",
             "reasoning": "tests pass", "branch": "autonomous/issue-1",
             "base_branch": "main"},
        ],
        "summary": "All issues resolved.",
    }
    parsed = RunCompleteInput.model_validate(payload)
    assert parsed.status == "success"
    assert len(parsed.verdicts) == 1
    assert parsed.verdicts[0].decision == "APPROVE"


def test_run_complete_input_rejects_missing_status():
    from agent.tools.run_complete import RunCompleteInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunCompleteInput.model_validate({"verdicts": [], "summary": "no status"})


def test_run_complete_input_rejects_unknown_decision():
    from agent.tools.run_complete import RunCompleteInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunCompleteInput.model_validate({
            "status": "success",
            "verdicts": [{"project": "owner/repo", "decision": "MAYBE"}],
            "summary": "x",
        })


def test_handler_returns_ack_for_valid_input():
    from agent.tools.run_complete import run_complete_handler

    result = asyncio.run(run_complete_handler.handler({
        "status": "success",
        "verdicts": [],
        "summary": "done",
    }))
    assert result.get("is_error", False) is False
    # MCP-shaped content list with at least one text block.
    contents = result.get("content", [])
    assert any(c.get("type") == "text" for c in contents)


def test_handler_returns_error_for_malformed_input():
    from agent.tools.run_complete import run_complete_handler

    result = asyncio.run(run_complete_handler.handler({"verdicts": []}))  # missing status + summary
    assert result.get("is_error") is True


def test_tool_name_constant_matches_decorator():
    from agent.tools.run_complete import RUN_COMPLETE_TOOL_NAME, run_complete_handler
    assert RUN_COMPLETE_TOOL_NAME == "RunComplete"
    # The SdkMcpTool object exposes .name on its dataclass.
    assert run_complete_handler.name == RUN_COMPLETE_TOOL_NAME


def test_build_run_complete_server_returns_mcp_config():
    from agent.tools.run_complete import build_run_complete_server

    server_config = build_run_complete_server()
    # McpSdkServerConfig dict shape (per claude_agent_sdk __init__).
    assert server_config["type"] == "sdk"
    assert server_config["name"] == "run_complete"


def test_streamstate_has_run_complete_payload_field():
    """_StreamState gains run_complete_payload (defaults to None)."""
    from agent.station_orchestrator import _StreamState
    state = _StreamState()
    assert hasattr(state, "run_complete_payload"), (
        "_StreamState must expose run_complete_payload (issue #385)"
    )
    assert state.run_complete_payload is None


def test_streamstate_can_latch_payload():
    from agent.station_orchestrator import _StreamState
    state = _StreamState()
    state.run_complete_payload = {"status": "success", "verdicts": [], "summary": "done"}
    assert state.run_complete_payload["status"] == "success"


def test_handle_stream_event_latches_run_complete_payload(monkeypatch):
    """A ToolUseBlock with name='RunComplete' latches the parsed payload onto state."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")

    monkeypatch.setattr("agent.station_orchestrator.post_webhook", lambda *a, **k: None)

    tool_use = ToolUseBlock(
        id="tu-1",
        name="RunComplete",
        input={
            "status": "success",
            "verdicts": [
                {"project": "owner/repo", "issue_number": 1, "decision": "APPROVE",
                 "branch": "autonomous/issue-1", "base_branch": "main", "reasoning": "ok"},
            ],
            "summary": "Done.",
        },
    )
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    assert state.run_complete_payload is not None
    assert state.run_complete_payload["status"] == "success"
    assert state.run_complete_payload["verdicts"][0]["decision"] == "APPROVE"


def test_handle_stream_event_ignores_malformed_run_complete(monkeypatch):
    """A malformed RunComplete tool call does NOT latch the payload."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    monkeypatch.setattr("agent.station_orchestrator.post_webhook", lambda *a, **k: None)

    # Missing required 'status' and 'summary' fields.
    tool_use = ToolUseBlock(id="tu-bad", name="RunComplete", input={"verdicts": []})
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    assert state.run_complete_payload is None, (
        "Malformed RunComplete must not latch the payload — lead should retry"
    )


def test_handle_stream_event_orchestrator_complete_emitted_with_verdicts(monkeypatch):
    """Once the payload latches, an orchestrator_complete webhook fires with the verdicts."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")

    captured: list[tuple] = []
    monkeypatch.setattr(
        "agent.station_orchestrator.post_webhook",
        lambda config, event, payload: captured.append((event, payload)),
    )

    tool_use = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    events = [e for (e, _p) in captured]
    assert "orchestrator_complete" in events, "RunComplete must emit orchestrator_complete"
    payload = next(p for (e, p) in captured if e == "orchestrator_complete")
    assert payload["status"] == "success"
    assert payload["summary"] == "done"
    assert "verdicts" in payload


def test_orchestrator_complete_emitted_exactly_once(monkeypatch):
    """A RunComplete tool call followed by a ResultMessage emits orchestrator_complete only once."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    captured: list[tuple] = []
    monkeypatch.setattr(
        "agent.station_orchestrator.post_webhook",
        lambda config, event, payload: captured.append((event, payload)),
    )

    tool_use = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    am = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(am, "session_id", "sess-1")
    handle_stream_event(am, config={}, run_id="run-x", log_file=None, state=state)

    rm = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50, is_error=False,
        num_turns=1, session_id="sess-1", total_cost_usd=0.0, usage=None,
        result="Final summary. All workers have completed.",
    )
    handle_stream_event(rm, config={}, run_id="run-x", log_file=None, state=state)

    oc_count = sum(1 for (e, _p) in captured if e == "orchestrator_complete")
    assert oc_count == 1, f"Expected exactly one orchestrator_complete; got {oc_count}"
