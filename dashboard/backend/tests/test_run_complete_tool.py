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
