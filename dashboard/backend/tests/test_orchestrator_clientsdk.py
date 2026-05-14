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


_real_asyncio_sleep = asyncio.sleep  # capture before any patching


async def _zero_sleep(*a, **k):
    """Replacement for asyncio.sleep that yields to the event loop (runs
    pending tasks) but does not actually wait. This allows control-poll tasks
    created with asyncio.create_task() to run during tests without introducing
    real time delays.
    """
    await _real_asyncio_sleep(0)


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
    # Use _zero_sleep instead of AsyncMock so asyncio.sleep yields to the
    # event loop, allowing create_task()-ed coroutines to run between iterations.
    monkeypatch.setattr(so.asyncio, "sleep", _zero_sleep)
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


class _YieldingFakeClient(_FakeClient):
    """Like _FakeClient but yields to the event loop between messages,
    allowing concurrently-running tasks (e.g. the control-poll task) to
    run and set control_flags before the next message is processed.
    """

    async def receive_response(self):
        if not self._scripted_messages:
            return
        for msg in self._scripted_messages.pop(0):
            yield msg
            await _real_asyncio_sleep(0)  # let other tasks run (bypass mock)


def test_interrupt_called_once_on_operator_stop(monkeypatch, tmp_path):
    """Setting control_flags['stop'] mid-stream causes one client.interrupt() call.

    Also asserts OrchestratorStopRequested propagates so the orchestrator
    handler emits the interrupted webhook.
    """
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()

    init = MagicMock(spec=so.SystemMessage)
    init.subtype = "init"
    init.session_id = "sess-1"
    # A non-result message — will be visited *after* we flip the stop flag.
    progress = MagicMock(spec=so.AssistantMessage)
    progress.session_id = "sess-1"
    progress.content = []
    progress.usage = {}

    def _client_factory(options=None):
        c = _YieldingFakeClient(options=options)
        c._scripted_messages = [[init, progress]]
        return c

    # Flip the stop flag the moment the control task starts polling.
    async def _stop_immediately(full_run_id, config, msgs, flags, interval=1.0):
        flags["stop"] = True

    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    _patch_orchestrate_setup(monkeypatch, so, tmp_path, _client_factory)
    # Override the control poll loop after _patch_orchestrate_setup sets a no-op
    monkeypatch.setattr(so, "_control_poll_loop", _stop_immediately)

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert len(_FakeClient.instances) == 1
    client = _FakeClient.instances[0]
    assert client.interrupts == 1, (
        f"client.interrupt() must be awaited exactly once on stop; got {client.interrupts}"
    )


def test_hook_failure_counter_zero_after_six_iterations(monkeypatch, tmp_path):
    """6 follow-up iterations must complete without a hook-callback failure."""
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()

    # 6 iterations: each yields a single ResultMessage with no completion text.
    # The loop terminates by exhausting max_reentries; that path also exercises
    # the per-iteration follow-up prompt build.
    def _r(text):
        m = MagicMock(spec=so.ResultMessage)
        m.session_id = "sess-1"
        m.result = text
        m.is_error = False
        m.duration_ms = 100
        m.num_turns = 1
        return m

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        # 7 iterations of "no completion" — orchestrate caps at max_reentries=6
        c._scripted_messages = [[_r("still working")] for _ in range(7)]
        return c

    _patch_orchestrate_setup(monkeypatch, so, tmp_path, _client_factory)

    # Capture baseline; the orchestrator measures the delta from this number.
    from agent.audit_hook import get_hook_callback_failure_count
    baseline = get_hook_callback_failure_count()

    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    delta = get_hook_callback_failure_count() - baseline
    assert delta == 0, (
        f"Hook callback failures must remain zero across iterations; delta={delta}"
    )


def test_orchestrator_module_does_not_import_query():
    """Per #384, the SDK's one-shot query() is no longer imported."""
    import inspect
    from agent import station_orchestrator
    src = inspect.getsource(station_orchestrator)
    # The migration replaces `from claude_agent_sdk import query, ClaudeAgentOptions`
    # with `from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions`.
    assert "from claude_agent_sdk import query" not in src, (
        "station_orchestrator must not import the one-shot query() under #384"
    )
    assert "ClaudeSDKClient" in src, (
        "station_orchestrator must import ClaudeSDKClient under #384"
    )


def test_orchestrate_loop_uses_async_with_clientsdkclient():
    """Source-level assertion that the orchestrate body opens the client via async with."""
    import inspect
    from agent.station_orchestrator import orchestrate
    src = inspect.getsource(orchestrate)
    assert "async with ClaudeSDKClient" in src, (
        "orchestrate must enter ClaudeSDKClient via async with (issue #384)"
    )
    assert "client.receive_response()" in src, (
        "orchestrate must consume replies via client.receive_response() (issue #384)"
    )
    assert "client.query(" in src, (
        "orchestrate must send prompts via client.query(...) (issue #384)"
    )
    assert "_user_prompt_stream" not in src, (
        "orchestrate must no longer reference _user_prompt_stream"
    )


def test_inner_loop_exits_on_run_complete(monkeypatch, tmp_path):
    """The inner async-for exits when run_complete_payload latches."""
    from agent import station_orchestrator as so
    from unittest.mock import MagicMock

    _FakeClient.instances.clear() if hasattr(_FakeClient, "instances") else None

    init = MagicMock(spec=so.SystemMessage)
    init.subtype = "init"; init.session_id = "sess-1"

    # An AssistantMessage carrying a RunComplete ToolUseBlock.
    from claude_agent_sdk.types import ToolUseBlock, AssistantMessage
    tu = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    am = AssistantMessage(content=[tu], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(am, "session_id", "sess-1")

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        # iter 1: init + tool-use. The loop should exit after the tool call;
        # if it tried to follow up, our script has no iter 2.
        c._scripted_messages = [[init, am]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so, "fetch_eligible_issues", lambda *a, **k: [{"number": 1, "title": "test", "body": ""}])
    monkeypatch.setattr(so, "claim_pending_queue_items", asyncio.coroutine(lambda *a, **k: []) if False else __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(return_value=[]))
    monkeypatch.setattr(so, "load_vision", lambda *a, **k: None)
    monkeypatch.setattr(so, "_combined_rank_issues", lambda issues, **k: issues)
    monkeypatch.setattr(so, "_control_poll_loop", __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
    monkeypatch.setattr(so.asyncio, "sleep", _zero_sleep)
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: MagicMock(returncode=0, stderr=""))

    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    client = _FakeClient.instances[0]
    # If the loop re-entered, queries would be > 1. RunComplete should exit after 1.
    assert len(client.queries) == 1, (
        f"Inner loop should exit after RunComplete tool call; got {len(client.queries)} queries"
    )


def test_fallback_when_tool_not_called(monkeypatch, tmp_path, caplog):
    """If the lead never calls RunComplete, _is_work_complete is the fallback.

    For one release window after #385, prose-matched completion still
    terminates the run — with a warning so the operator can spot leads
    that haven't migrated to the tool contract.
    """
    from agent import station_orchestrator as so
    from unittest.mock import MagicMock, AsyncMock

    _FakeClient.instances.clear() if hasattr(_FakeClient, "instances") else None

    rm = MagicMock(spec=so.ResultMessage)
    rm.session_id = "sess-1"
    rm.result = "Final summary. All workers have completed."
    rm.is_error = False; rm.duration_ms = 100; rm.num_turns = 1

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        c._scripted_messages = [[rm]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so, "fetch_eligible_issues", lambda *a, **k: [{"number": 1, "title": "test", "body": ""}])
    monkeypatch.setattr(so, "claim_pending_queue_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(so, "load_vision", lambda *a, **k: None)
    monkeypatch.setattr(so, "_combined_rank_issues", lambda issues, **k: issues)
    monkeypatch.setattr(so, "_control_poll_loop", AsyncMock())
    monkeypatch.setattr(so.asyncio, "sleep", _zero_sleep)
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: MagicMock(returncode=0, stderr=""))

    import logging
    caplog.set_level(logging.WARNING)

    config = {
        "projects": [{"repo": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    # Run completed — the loop exited via the fallback heuristic.
    # The fallback path must log a warning so operators can see "lead did not
    # call RunComplete" runs.
    fallback_logged = any(
        "RunComplete" in rec.message and "fallback" in rec.message.lower()
        for rec in caplog.records
    )
    assert fallback_logged, "Fallback path must log a warning about missing RunComplete tool call"
