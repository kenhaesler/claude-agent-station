"""Tests for the ClaudeSDKClient-based orchestrate loop (issue #384)."""
from __future__ import annotations

import importlib


def test_orchestrator_no_longer_exposes_user_prompt_stream():
    """_user_prompt_stream is deleted as part of the ClaudeSDKClient migration."""
    mod = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(mod, "_user_prompt_stream"), (
        "_user_prompt_stream must be removed (issue #384) — it was a hack to "
        "keep stdin open across teammate spawns under query(). ClaudeSDKClient "
        "owns stdin for the lifetime of `async with`."
    )


def test_orchestrator_no_longer_exposes_force_exit_with_cleanup():
    """_force_exit_with_cleanup is deleted as part of the ClaudeSDKClient migration."""
    mod = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(mod, "_force_exit_with_cleanup"), (
        "_force_exit_with_cleanup must be removed (issue #384) — "
        "ClaudeSDKClient.__aexit__ owns subprocess teardown."
    )


def test_orchestrator_imports_clientsdk():
    """The migration replaces `query` with `ClaudeSDKClient`."""
    mod = importlib.import_module("agent.station_orchestrator")
    # The symbol is re-exported at module level after `from claude_agent_sdk import ...`
    assert hasattr(mod, "ClaudeSDKClient"), (
        "ClaudeSDKClient must be imported from claude_agent_sdk (issue #384)."
    )
