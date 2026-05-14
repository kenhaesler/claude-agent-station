# ClaudeSDKClient Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-shot `claude_agent_sdk.query()` call in `agent.station_orchestrator.orchestrate` with a long-lived `ClaudeSDKClient` session, deleting the `_user_prompt_stream` keep-stdin-open hack and the `_force_exit_with_cleanup` PID-walking shutdown.

**Architecture:** A single `async with ClaudeSDKClient(options=options) as client` block now spans the entire per-project re-entry loop. Each iteration sends the prompt with `await client.query(...)` and consumes messages with `async for message in client.receive_response()`. Operator stops call `await client.interrupt()` rather than relying on stdin closure; the client's `__aexit__` is the single lifecycle owner, so `asyncio.run()` finalisers can run cleanly and no `/proc` walk is needed.

**Tech Stack:** Python 3.11+, `claude_agent_sdk.ClaudeSDKClient`, `pytest` + `pytest-asyncio`, existing audit hooks unchanged.

---

## File Structure

| Path | Responsibility |
|---|---|
| `agent/station_orchestrator.py` | Delete `_user_prompt_stream` (lines 81–137); delete `_force_exit_with_cleanup` (lines 2460–2505); rewrite the per-project re-entry loop (lines 1937–2118) to drive a single `ClaudeSDKClient`; remove `options.resume`/`options.continue_conversation` assignments; switch `main()` to plain `sys.exit(asyncio.run(...))`; replace the `from claude_agent_sdk import query, ClaudeAgentOptions` import with `from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions` (the `query` import is unused once the rewrite lands). |
| `dashboard/backend/tests/test_orchestrator_clientsdk.py` | **New** — unit tests for the new `ClaudeSDKClient`-based loop (lifecycle, follow-up, interrupt, no-resume, hook-failure-counter). |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | Update to assert `ClaudeSDKClient` is imported instead of `query`; assert `_user_prompt_stream` and `_force_exit_with_cleanup` symbols are absent from the module. |
| `dashboard/backend/tests/test_force_exit_cleanup.py` | **Delete** — the function it tests is gone. |

---

## Tasks

### Task 1 — Lock current behaviour with a regression assertion before rewriting

The first task captures the current `_user_prompt_stream` / `_force_exit_with_cleanup` presence as a *failing* test we will flip after deletion. Using TDD here means the deletion can't accidentally leave dead symbols behind.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_orchestrator_clientsdk.py` with this content:

```python
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
```

**Step 2: Run the test — confirm it fails.**

```
$ cd /home/simon/Documents/claude-agent-station
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py -v
```

Expected output (3 failures):

```
FAILED ... test_orchestrator_no_longer_exposes_user_prompt_stream - AssertionError: _user_prompt_stream must be removed ...
FAILED ... test_orchestrator_no_longer_exposes_force_exit_with_cleanup - AssertionError: _force_exit_with_cleanup must be removed ...
FAILED ... test_orchestrator_imports_clientsdk - AssertionError: ClaudeSDKClient must be imported ...
```

**Step 3: Minimal implementation — flip the imports.**

In `agent/station_orchestrator.py`, replace the existing import block at lines 31–43:

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolUseBlock,
)
```

with:

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolUseBlock,
)
```

Then delete the entire `async def _user_prompt_stream(text: str):` function — lines 81 through 137 inclusive (the `async def` line through the `await asyncio.sleep(3600)` call). Also delete the `def _force_exit_with_cleanup(exit_code: int) -> None:` function at lines 2460–2505 inclusive.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py -v
```

Expected:

```
PASSED ... test_orchestrator_no_longer_exposes_user_prompt_stream
PASSED ... test_orchestrator_no_longer_exposes_force_exit_with_cleanup
PASSED ... test_orchestrator_imports_clientsdk
```

The orchestrate loop still references these deleted symbols, so `python -m pytest` against the wider suite will fail. That's expected — Tasks 2–7 fix it.

**Step 5: Commit.**

```
$ git add dashboard/backend/tests/test_orchestrator_clientsdk.py agent/station_orchestrator.py
$ git commit -m "refactor(orchestrator): drop _user_prompt_stream and _force_exit_with_cleanup imports"
```

---

### Task 2 — Switch `main()` to a plain `asyncio.run` (no `_force_exit_with_cleanup` wrapper)

`main()` currently calls `_force_exit_with_cleanup(exit_code)` after `asyncio.run(orchestrate(...))`. That symbol no longer exists — the next test asserts `main()` is clean.

**Step 1: Add the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
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
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_main_returns_via_sys_exit_asyncio_run -v
```

Expected:

```
FAILED ... test_main_returns_via_sys_exit_asyncio_run - AssertionError: main() should return through sys.exit(asyncio.run(orchestrate( ...
```

(The current code is `_force_exit_with_cleanup(exit_code)`, not `sys.exit(asyncio.run(...))`.)

**Step 3: Implementation — rewrite `main()`'s non-driver branch.**

In `agent/station_orchestrator.py`, find the `main()` function (currently around line 2508). Replace the existing two-line non-driver tail:

```python
    # Existing Agent Teams orchestration path — unchanged.
    config = load_config(args.config)
    exit_code = asyncio.run(orchestrate(config, args.run_id, args.workspaces_dir))
    _force_exit_with_cleanup(exit_code)
```

with:

```python
    # Existing Agent Teams orchestration path. ClaudeSDKClient (#384) owns
    # subprocess teardown via its __aexit__, so asyncio.run() can finalise
    # cleanly without the /proc-walk shutdown hack.
    config = load_config(args.config)
    sys.exit(asyncio.run(orchestrate(config, args.run_id, args.workspaces_dir)))
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_main_returns_via_sys_exit_asyncio_run -v
```

Expected:

```
PASSED ... test_main_returns_via_sys_exit_asyncio_run
```

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "refactor(orchestrator): main() exits via sys.exit(asyncio.run(...))"
```

---

### Task 3 — Introduce the `ClaudeSDKClient` context, build options once

The current loop builds `ClaudeAgentOptions(...)` *inside* the `for iteration in range(max_reentries)` block and runs a fresh `query(...)` per iteration. The migration target builds options once before the loop and enters one `ClaudeSDKClient` for the entire per-project lifetime.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
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


def test_client_opens_and_closes_once_per_project(monkeypatch, tmp_path):
    """ClaudeSDKClient is entered exactly once per project iteration."""
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()
    # One scripted iteration: a SystemMessage(init) + a ResultMessage with
    # work-complete text. Wire enough of the project setup to reach the loop.
    config = {
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
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

    monkeypatch.setattr(so, "ClaudeSDKClient", _FakeClient)
    # Bypass the heavyweight setup paths we are not testing here.
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)

    # Pre-load the script.
    def _client_factory(options=None):
        c = _FakeClient(options=options)
        c._scripted_messages = [[init_msg, result_msg]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)

    # Minimal orchestrate invocation — passes config + run_id + workspaces_dir.
    # Some adapters (audit hooks, vision) need monkeypatching to no-op; defer
    # those to the integration test. This test only asserts the client is
    # instantiated and entered exactly once.
    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert len(_FakeClient.instances) == 1, (
        f"Expected exactly one ClaudeSDKClient per project; got {len(_FakeClient.instances)}"
    )
    client = _FakeClient.instances[0]
    assert client.entered == 1 and client.exited == 1, (
        f"Client lifecycle should open and close once; entered={client.entered}, exited={client.exited}"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_client_opens_and_closes_once_per_project -v
```

Expected failure: the loop still calls `query(prompt=..., options=...)`, no `ClaudeSDKClient` is ever instantiated, and the test errors out on the `assert len(_FakeClient.instances) == 1` check (or earlier if `query` is called and breaks the fake stream).

**Step 3: Implementation — rewrite the inner loop.**

In `agent/station_orchestrator.py`, replace the inner stream loop (lines 1966–2118 in the pre-migration tree — the `with open(stream_log_path, "a") as log_file:` … `await asyncio.sleep(15)` block). The replacement:

```python
            with open(stream_log_path, "a") as log_file:
                # Build options once — ClaudeSDKClient owns the session for
                # the lifetime of `async with`, so resume tokens are unnecessary.
                options = ClaudeAgentOptions(
                    cwd=workspace,
                    env={
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        "GITHUB_REPO": repo,
                    },
                    mcp_servers={
                        "playwright": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@playwright/mcp@latest"],
                        },
                        "ref": {
                            "type": "http",
                            "url": "https://api.ref.tools/mcp",
                        },
                    },
                    allowed_tools=["Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent", "mcp__playwright__*", "mcp__ref__*"],
                    max_turns=manager_turns,
                    model=manager_model,
                    agents=agents_dict,
                    can_use_tool=make_audited_policy(
                        run_id=f"run-{run_id}",
                        level=autonomy_level,
                        agent_id="lead",
                    ),
                    hooks={
                        "PreToolUse": [HookMatcher(hooks=[
                            make_pre_tool_hook(
                                run_id=f"run-{run_id}",
                                actor="lead",
                                trace_id=f"run-{run_id}",
                            ),
                        ])],
                        "PostToolUse": [HookMatcher(hooks=[
                            make_post_tool_hook(
                                run_id=f"run-{run_id}",
                                actor="lead",
                            ),
                        ])],
                    },
                    max_budget_usd=max_budget_usd,
                )

                stop_signalled = False
                async with ClaudeSDKClient(options=options) as client:
                    for iteration in range(max_reentries):
                        is_followup = iteration > 0

                        if control_flags["stop"]:
                            logger.info("Stop requested before iteration %d", iteration + 1)
                            break

                        if is_followup:
                            prompt = build_followup_prompt(
                                workspace,
                                operator_messages=pending_operator_messages,
                            )
                            pending_operator_messages.clear()
                            logger.info(
                                "Re-entering lead session (iteration %d/%d)",
                                iteration + 1, max_reentries,
                            )
                        else:
                            prompt = build_team_prompt(
                                repo, issues, config, run_id, workspace, worktree_paths,
                                vision=vision, project_mode=project_mode,
                                approved_plan_paths=approved_plan_paths,
                            )

                        await client.query(prompt)

                        async for message in client.receive_response():
                            sid = getattr(message, "session_id", None)
                            if sid and stream_state.main_session_id is None:
                                stream_state.main_session_id = sid
                                logger.info(
                                    "Captured lead session_id=%s for run-%s",
                                    sid, run_id,
                                )

                            if isinstance(message, SystemMessage) and getattr(message, "subtype", "") == "init":
                                if not first_init_sent:
                                    post_webhook(config, "orchestrator_start", {
                                        "run_id": f"run-{run_id}",
                                        "mode": project_mode,
                                    })
                                    first_init_sent = True

                            handle_stream_event(message, config, run_id, log_file=log_file, state=stream_state)

                            if control_flags["stop"] and not stop_signalled:
                                stop_signalled = True
                                logger.info("Stop requested; interrupting client")
                                await client.interrupt()
                                break

                            # Completion gate — still text-heuristic; #385 replaces it
                            # with the structured RunComplete tool. The natural exit
                            # of receive_response() handles the SDK-side teardown.
                            if isinstance(message, ResultMessage):
                                result_text = getattr(message, "result", "")
                                if _is_work_complete(result_text):
                                    work_complete = True
                                    logger.info("Work-complete signal received")
                                    break

                        if control_flags["stop"]:
                            raise OrchestratorStopRequested()

                        if work_complete:
                            logger.info("Agent Teams orchestration completed for %s", repo)
                            break

                        # Brief pause before the next follow-up turn. The control
                        # task keeps running during this sleep.
                        await asyncio.sleep(15)
```

Note: this also removes the `options.resume = session_id` / `options.continue_conversation = True` block — the single client owns the session, so resume tokens are unnecessary. The `session_id` capture is kept because `handle_stream_event` still uses `state.main_session_id` for sub-session filtering.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_client_opens_and_closes_once_per_project -v
```

Expected:

```
PASSED ... test_client_opens_and_closes_once_per_project
```

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "refactor(orchestrator): drive Agent Teams loop via ClaudeSDKClient"
```

---

### Task 4 — Verify follow-up iterations reuse the same client (no resume token)

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
def test_followup_uses_same_client_no_resume(monkeypatch, tmp_path):
    """Iteration 2+ calls client.query() on the *same* client; no resume token is set."""
    from agent import station_orchestrator as so

    _FakeClient.instances.clear()
    config = {
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
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

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so, "asyncio_sleep", AsyncMock(), raising=False)
    # Speed up the 15s idle wait between iterations.
    monkeypatch.setattr(so.asyncio, "sleep", AsyncMock())

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
```

**Step 2: Run the test — confirm it fails (if it does).**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_followup_uses_same_client_no_resume -v
```

Expected: passes on first run because Task 3's implementation already meets the contract. **If it fails**, that means Task 3's rewrite still references `options.resume` somewhere — find the leftover assignment and remove it.

**Step 3: If failing, remove residual `options.resume` / `options.continue_conversation` writes.**

Grep:

```
$ grep -n "options.resume\|continue_conversation" agent/station_orchestrator.py
```

Expected: zero matches after Task 3. If any exist, delete them.

**Step 4: Re-run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_followup_uses_same_client_no_resume -v
```

Expected:

```
PASSED ... test_followup_uses_same_client_no_resume
```

**Step 5: Commit.**

```
$ git add dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "test(orchestrator): assert follow-up turns reuse the same ClaudeSDKClient with no resume token"
```

---

### Task 5 — Operator stop → `client.interrupt()` is awaited once

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
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
        c = _FakeClient(options=options)
        c._scripted_messages = [[init, progress]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so.asyncio, "sleep", AsyncMock())

    # Flip the stop flag the moment the control task starts polling.
    async def _stop_immediately(full_run_id, config, msgs, flags, interval=1.0):
        flags["stop"] = True

    monkeypatch.setattr(so, "_control_poll_loop", _stop_immediately)

    config = {
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    assert len(_FakeClient.instances) == 1
    client = _FakeClient.instances[0]
    assert client.interrupts == 1, (
        f"client.interrupt() must be awaited exactly once on stop; got {client.interrupts}"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_interrupt_called_once_on_operator_stop -v
```

If Task 3's loop already calls `await client.interrupt()` exactly once on stop and the test passes, jump to Step 4. Otherwise verify the rewrite from Task 3 includes the `if control_flags["stop"] and not stop_signalled:` guard.

**Step 3: Implementation — ensure idempotent interrupt.**

In the inner `async for message in client.receive_response()` block in `agent/station_orchestrator.py`, confirm the stop branch matches:

```python
                            if control_flags["stop"] and not stop_signalled:
                                stop_signalled = True
                                logger.info("Stop requested; interrupting client")
                                await client.interrupt()
                                break
```

The `stop_signalled` latch prevents a second interrupt on the next iteration's pass over the same flag. If this guard is missing, add it now.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_interrupt_called_once_on_operator_stop -v
```

Expected:

```
PASSED ... test_interrupt_called_once_on_operator_stop
```

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "feat(orchestrator): operator stop drives client.interrupt() exactly once"
```

---

### Task 6 — Hook-callback-failure counter is zero after a multi-iteration run

The original bug (`Error: Stream closed` from `cli.js:7552 sendRequest`) showed up as a non-zero `get_hook_callback_failure_count()` after a long Agent Teams session. With `ClaudeSDKClient` owning stdin across every `client.query()`, the counter must stay at the baseline.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
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

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so.asyncio, "sleep", AsyncMock())

    # Capture baseline; the orchestrator measures the delta from this number.
    from agent.audit_hook import get_hook_callback_failure_count
    baseline = get_hook_callback_failure_count()

    config = {
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
    }

    asyncio.run(so.orchestrate(config, "20260514T120000Z", str(tmp_path)))

    delta = get_hook_callback_failure_count() - baseline
    assert delta == 0, (
        f"Hook callback failures must remain zero across iterations; delta={delta}"
    )
```

**Step 2: Run the test — confirm it fails (or passes — depends on whether the fake stream can drive a hook-failure).**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_hook_failure_counter_zero_after_six_iterations -v
```

For a fake stream, the test should pass with the new client implementation. If it fails because `get_hook_callback_failure_count()` is not zero, the `make_pre_tool_hook` / `make_post_tool_hook` paths are still being invoked with a closed stream — that means the client teardown is firing too early. Re-inspect the rewrite from Task 3 and confirm the `async with` block wraps the *entire* per-project loop, not a single iteration.

**Step 3: If failing, widen the `async with` block.**

Confirm in `agent/station_orchestrator.py` that the structure is:

```python
async with ClaudeSDKClient(options=options) as client:
    for iteration in range(max_reentries):
        await client.query(prompt)
        async for message in client.receive_response():
            ...
```

and **not** the inverse (one client per iteration). The single-client structure is the whole point of #384.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_hook_failure_counter_zero_after_six_iterations -v
```

Expected:

```
PASSED ... test_hook_failure_counter_zero_after_six_iterations
```

**Step 5: Commit.**

```
$ git add dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "test(orchestrator): assert zero hook-callback failures across the new ClaudeSDKClient loop"
```

---

### Task 7 — Update `test_orchestrator_wiring.py`, delete `test_force_exit_cleanup.py`

**Step 1: Write the failing test.**

In `dashboard/backend/tests/test_orchestrator_wiring.py`, locate any existing assertions that import or reference `_user_prompt_stream` or `_force_exit_with_cleanup`. Run:

```
$ grep -n "_user_prompt_stream\|_force_exit_with_cleanup" dashboard/backend/tests/test_orchestrator_wiring.py
```

If matches exist, those tests will now fail because the symbols are gone. The task is to update the assertions: change positive-existence checks to absence checks. For each matching block, replace it with:

```python
def test_orchestrator_wiring_no_user_prompt_stream():
    """Per #384, _user_prompt_stream no longer exists."""
    import importlib
    so = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(so, "_user_prompt_stream")


def test_orchestrator_wiring_no_force_exit_with_cleanup():
    """Per #384, _force_exit_with_cleanup no longer exists."""
    import importlib
    so = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(so, "_force_exit_with_cleanup")
```

If no matches exist, append those two tests at the bottom of the file regardless — they're cheap regression guards for the deletion.

**Step 2: Run the updated wiring file plus the delete-old-test step.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_wiring.py -v
```

Expected: passes. Any leftover stale assertions failed previously; with the rewrites above, they're now consistent with the migration.

Then delete the obsolete file:

```
$ git rm dashboard/backend/tests/test_force_exit_cleanup.py
```

**Step 3: Implementation — confirm the wiring file builds.**

Run a quick collection sweep to catch any import-time failures in the test directory:

```
$ python -m pytest dashboard/backend/tests/ --collect-only -q
```

Expected: zero collection errors. If any test file fails to import because it expected `_user_prompt_stream`, fix it the same way.

**Step 4: Run the full orchestrator-related suite.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_wiring.py dashboard/backend/tests/test_orchestrator_clientsdk.py -v
```

Expected: all pass.

**Step 5: Commit.**

```
$ git add dashboard/backend/tests/test_orchestrator_wiring.py
$ git rm dashboard/backend/tests/test_force_exit_cleanup.py
$ git commit -m "test(orchestrator): update wiring tests for the ClaudeSDKClient migration"
```

---

### Task 8 — Documentation: update `docs/configuration.md` and `docs/architecture.md`

`CLAUDE.md` requires docs to track code changes. The `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` env var is still set in `agent/launcher.py:339` (its removal is #392), but `_user_prompt_stream` / `_force_exit_with_cleanup` were almost certainly mentioned in the lifecycle docs.

**Step 1: Identify drifted sections.**

```
$ grep -rn "_user_prompt_stream\|_force_exit_with_cleanup\|claude_agent_sdk.query\|ClaudeSDKClient" docs/
```

For each match in `docs/architecture.md` or `docs/configuration.md`, plan a replacement: the orchestrator now uses `ClaudeSDKClient` as a long-lived session, and the `/proc`-walking shutdown is gone.

**Step 2: Apply edits.**

Use the `Edit` tool for each docs file. The general substitution shape:

- Before: text claiming `query()` runs the Agent Teams session, possibly mentioning the keep-stdin-open generator.
- After: text describing the `async with ClaudeSDKClient` block as the per-project lifetime owner.

Concretely, replace any sentence shaped like:

> The orchestrator drives the lead agent via `claude_agent_sdk.query()`, with `_user_prompt_stream` keeping stdin open across teammate spawns.

with:

> The orchestrator drives the lead agent via a long-lived `ClaudeSDKClient` session (`async with ClaudeSDKClient(options=options) as client`). Each re-entry iteration sends the next user message with `await client.query(...)` and consumes replies with `async for message in client.receive_response()`. The client's context-manager exit reaps the bundled CLI subprocess; no PID-walking shutdown is needed.

**Step 3: Verify.**

```
$ grep -rn "_user_prompt_stream\|_force_exit_with_cleanup" docs/
```

Expected: zero matches (or only historical references explicitly labelled "pre-#384").

**Step 4: Sanity check the renders.**

```
$ python -m markdown docs/architecture.md > /dev/null && echo OK
$ python -m markdown docs/configuration.md > /dev/null && echo OK
```

Expected:

```
OK
OK
```

(If `markdown` isn't installed, skip — the docs are markdown, not generated.)

**Step 5: Commit.**

```
$ git add docs/
$ git commit -m "docs: update architecture + configuration for ClaudeSDKClient lifecycle"
```

---

### Task 9 — Smoke parity: orchestrator module imports cleanly, full test suite green

The final gate before the PR is opened: the orchestrator module imports without `query` references, the wider test suite still passes, and a runtime smoke of `orchestrate` against a stub client completes end-to-end.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py`:

```python
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
```

**Step 2: Run the test — confirm it passes (the implementation tasks above already cover the contract).**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_orchestrator_module_does_not_import_query dashboard/backend/tests/test_orchestrator_clientsdk.py::test_orchestrate_loop_uses_async_with_clientsdkclient -v
```

Expected:

```
PASSED ... test_orchestrator_module_does_not_import_query
PASSED ... test_orchestrate_loop_uses_async_with_clientsdkclient
```

If either fails, the implementation in Task 3 left a residual reference — find it via `grep -n "query\b" agent/station_orchestrator.py` and remove.

**Step 3: Implementation — final grep audit.**

```
$ grep -n "from claude_agent_sdk import query\|_user_prompt_stream\|_force_exit_with_cleanup" agent/station_orchestrator.py
```

Expected: zero matches.

**Step 4: Run the full backend test suite.**

```
$ cd /home/simon/Documents/claude-agent-station
$ python -m pytest dashboard/backend/tests/ -q
```

Expected: all tests pass, including the pre-existing 29 regression tests from PR #381 (now adapted) plus the new `test_orchestrator_clientsdk.py` tests added across Tasks 1–9.

If a pre-existing test fails because it asserted on the bash-era `_is_work_complete` break path, the assertion needs to be re-anchored on the `ClaudeSDKClient` flow. The contract is: `_is_work_complete` still gates the loop exit in this PR (it's #385's job to remove it).

**Step 5: Commit and tag the PR-ready state.**

```
$ git add dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "test(orchestrator): final wiring + receive_response assertions for ClaudeSDKClient migration"
```

Open the PR:

```
$ gh pr create --base dev --title "refactor(orchestrator): migrate to ClaudeSDKClient (#384)" --body "Closes #384. Replaces the one-shot query() loop with a long-lived ClaudeSDKClient. Deletes _user_prompt_stream and _force_exit_with_cleanup. Per memory, PRs target dev."
```

---

## Verification checklist (run before requesting review)

- [ ] `grep -n "_user_prompt_stream\|_force_exit_with_cleanup\|from claude_agent_sdk import query" agent/station_orchestrator.py` → zero matches.
- [ ] `python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py -v` → all pass.
- [ ] `python -m pytest dashboard/backend/tests/test_orchestrator_wiring.py -v` → all pass.
- [ ] `python -m pytest dashboard/backend/tests/ -q` → suite green.
- [ ] `git ls-files dashboard/backend/tests/test_force_exit_cleanup.py` → empty (file removed).
- [ ] `grep -rn "_user_prompt_stream\|_force_exit_with_cleanup" docs/` → zero matches outside historical labels.
