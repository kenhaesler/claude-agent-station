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


def test_team_prompt_includes_run_complete_contract():
    """build_team_prompt must mention RunComplete in its authoritative contract."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="owner/repo",
        issues=[{"number": 1, "title": "x", "labels": []}],
        config={"models": {}, "limits": {}},
        run_id="run-x",
        workspace="/tmp/ws",
        worktree_paths={},
        vision=None,
        project_mode="single",
        approved_plan_paths=[],
    )
    assert "RunComplete" in prompt, (
        "build_team_prompt must instruct the lead to call RunComplete (issue #385)"
    )
    # Status values must be documented in the prompt so the lead picks the right one.
    assert "success" in prompt and "partial" in prompt and "blocked" in prompt


def test_followup_prompt_includes_run_complete_contract():
    from agent.station_orchestrator import build_followup_prompt
    prompt = build_followup_prompt(workspace="/tmp/ws", operator_messages=[])
    assert "RunComplete" in prompt, (
        "build_followup_prompt must keep the RunComplete contract on every iteration"
    )


def test_orchestrate_registers_run_complete_server(monkeypatch, tmp_path):
    """ClaudeAgentOptions.mcp_servers must include 'run_complete' and allowed_tools includes it."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    import subprocess as _sp
    from agent import station_orchestrator as so

    captured_options: list = []

    class _FakeClient:
        def __init__(self, *, options=None):
            captured_options.append(options)

        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def query(self, prompt): pass
        async def receive_response(self):
            init = MagicMock(spec=so.SystemMessage)
            init.subtype = "init"; init.session_id = "sess-1"
            yield init
        async def interrupt(self): pass

    monkeypatch.setattr(so, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so, "fetch_eligible_issues", lambda *a, **k: [{"number": 1, "title": "test", "body": ""}])
    monkeypatch.setattr(so, "claim_pending_queue_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(so, "load_vision", lambda *a, **k: None)
    monkeypatch.setattr(so, "_combined_rank_issues", lambda issues, **k: issues)
    monkeypatch.setattr(so, "build_team_prompt", lambda *a, **k: "test prompt")
    monkeypatch.setattr(so, "build_followup_prompt", lambda *a, **k: "followup prompt")
    monkeypatch.setattr(so, "handle_stream_event", lambda *a, **k: None)
    monkeypatch.setattr(so, "_control_poll_loop", AsyncMock())
    monkeypatch.setattr(so.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(_sp, "run", lambda *a, **k: MagicMock(returncode=0, stderr=""))

    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert captured_options, "Expected ClaudeAgentOptions to be built and captured"
    opts = captured_options[0]
    assert "run_complete" in (opts.mcp_servers or {}), (
        "mcp_servers must include 'run_complete' (issue #385)"
    )
    allowed = opts.allowed_tools or []
    assert any("RunComplete" in t for t in allowed), (
        "allowed_tools must include mcp__run_complete__RunComplete"
    )
