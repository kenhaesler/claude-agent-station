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


def test_main_returns_via_sys_exit_asyncio_run():
    """main() in the non-driver branch should use sys.exit(asyncio.run(...)).

    After #384, the _force_exit_with_cleanup wrapper is gone — the client's
    __aexit__ is the only teardown the orchestrator needs.
    """
    import inspect
    from agent import station_orchestrator
    src = inspect.getsource(station_orchestrator.main)
    assert "_force_exit_with_cleanup" not in src, (
        "main() must not call _force_exit_with_cleanup (issue #384)."
    )
    assert "asyncio.run(orchestrate(" in src, (
        "main() should still drive orchestrate via asyncio.run."
    )
    assert "sys.exit(asyncio.run(orchestrate(" in src, (
        "main() should return through sys.exit(asyncio.run(...))."
    )
