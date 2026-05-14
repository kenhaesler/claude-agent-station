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


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeClient:
    """Minimal stand-in for ClaudeSDKClient used by orchestrate tests."""

    instances: list["_FakeClient"] = []

    def __init__(self, *, options=None):
        self.options = options
        self.entered = 0
        self.exited = 0
        self.queries: list[str] = []
        self.interrupts = 0
        self._scripted_messages: list[list[object]] = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        return False

    async def query(self, prompt: str):
        self.queries.append(prompt)

    async def receive_response(self):
        if not self._scripted_messages:
            return
        for msg in self._scripted_messages.pop(0):
            yield msg

    async def interrupt(self):
        self.interrupts += 1


def _patch_orchestrate_setup(monkeypatch, so, tmp_path, client_factory):
    """Shared monkeypatching helper: bypasses the heavyweight orchestrate setup
    so tests can focus on the ClaudeSDKClient lifecycle.
    """
    monkeypatch.setattr(so, "ClaudeSDKClient", client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: None)
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
    # subprocess calls during worktree setup — no-op them
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: MagicMock(returncode=0, stderr=""))


def test_client_opens_and_closes_once_per_project(monkeypatch, tmp_path):
    """ClaudeSDKClient is entered exactly once per project iteration."""
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()
    # One scripted iteration: a SystemMessage(init) + a ResultMessage with
    # work-complete text. Wire enough of the project setup to reach the loop.
    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    init_msg = MagicMock(spec=so.SystemMessage)
    init_msg.subtype = "init"
    init_msg.session_id = "sess-1"
    result_msg = MagicMock(spec=so.ResultMessage)
    result_msg.session_id = "sess-1"
    result_msg.result = "All teammates have completed. Final summary."
    result_msg.is_error = False
    result_msg.duration_ms = 100
    result_msg.num_turns = 1

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        c._scripted_messages = [[init_msg, result_msg]]
        return c

    _patch_orchestrate_setup(monkeypatch, so, tmp_path, _client_factory)

    # Minimal orchestrate invocation — passes config + run_id + workspaces_dir.
    # This test only asserts the client is instantiated and entered exactly once.
    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert len(_FakeClient.instances) == 1, (
        f"Expected exactly one ClaudeSDKClient per project; got {len(_FakeClient.instances)}"
    )
    client = _FakeClient.instances[0]
    assert client.entered == 1 and client.exited == 1, (
        f"Client lifecycle should open and close once; entered={client.entered}, exited={client.exited}"
    )


def test_followup_uses_same_client_no_resume(monkeypatch, tmp_path):
    """Iteration 2+ calls client.query() on the *same* client; no resume token is set."""
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()
    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    # Iteration 1: ResultMessage with no work-complete text → loop re-enters.
    # Iteration 2: ResultMessage with work-complete text → loop exits.
    def _msg(session_id, result_text):
        m = MagicMock(spec=so.ResultMessage)
        m.session_id = session_id
        m.result = result_text
        m.is_error = False
        m.duration_ms = 100
        m.num_turns = 1
        return m

    iter1 = [_msg("sess-1", "still working")]
    iter2 = [_msg("sess-1", "Final summary. All workers have completed.")]

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        c._scripted_messages = [iter1, iter2]
        return c

    _patch_orchestrate_setup(monkeypatch, so, tmp_path, _client_factory)

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert len(_FakeClient.instances) == 1, "Same client must be reused across follow-up turns"
    client = _FakeClient.instances[0]
    assert len(client.queries) == 2, (
        f"Expected one query per iteration (initial + 1 follow-up); got {len(client.queries)}"
    )
    # The options passed to the constructor must not set resume / continue_conversation.
    assert getattr(client.options, "resume", None) is None, (
        "options.resume should never be set under ClaudeSDKClient — one client persists the session"
    )
    assert getattr(client.options, "continue_conversation", False) is False, (
        "options.continue_conversation should never be set under ClaudeSDKClient"
    )
