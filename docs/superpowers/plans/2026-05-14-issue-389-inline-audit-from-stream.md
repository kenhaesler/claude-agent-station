# Inline Audit Writes from Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SDK `PreToolUse` / `PostToolUse` hook callbacks that write `audit_log` rows with inline writes from `handle_stream_event`, so every tool call produces a complete audit row even when the bundled CLI drops a hook delivery mid-stream.

**Architecture:** `ToolUseBlock` items inside `AssistantMessage.content` trigger an `audit_log` "started" row; `ToolResultBlock` items inside `UserMessage.content` trigger the matching "finished" row. Both writes happen on the orchestrator's event loop thread via `asyncio.to_thread` so SQLite WAL contention does not stall the stream. Hook factories, the `_HOOK_CB_FAILURE_COUNT` counter, and the `audit_dropouts` / `hook_failures` webhook plumbing are deleted as dead code. The `can_use_tool` callback (autonomy policy + `auto_mode_decision` rows) is untouched — different SDK mechanism, different failure profile.

**Tech Stack:** Python 3.11+, `claude_agent_sdk.types` (`AssistantMessage`, `UserMessage`, `ToolUseBlock`, `ToolResultBlock`), `asyncio.to_thread`, raw `sqlite3`, pytest with hand-built SDK message fixtures.

**Spec:** `docs/superpowers/specs/2026-05-14-issue-389-inline-audit-from-stream.md`

**Tracking issue:** [#389](https://github.com/kenhaesler/agent-station/issues/389)

**Hard dependency:** Issue [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) (`ClaudeSDKClient` migration). This plan assumes the orchestrator's stream-iteration point is `async for message in client.receive_response()` and that the `query(prompt=_user_prompt_stream(...))` call at `station_orchestrator.py:2047` is gone.

---

## File Structure

| File | Modification | Responsibility |
|---|---|---|
| `agent/audit_hook.py` | edit | Add `write_audit_started_from_block(*, run_id, actor, block, trace_id, db_path)` and `write_audit_finished_from_block(*, block, db_path)`. Both delegate to the existing `write_audit_start` / `write_audit_finish` writers. Delete `make_pre_tool_hook`, `make_post_tool_hook`, `_record_hook_failure`, `_HOOK_CB_FAILURE_COUNT`, and `get_hook_callback_failure_count`. |
| `agent/station_orchestrator.py` | edit | `handle_stream_event` becomes `async`. Add a `UserMessage` branch that walks `ToolResultBlock` items. The existing `ToolUseBlock` branch adds an `await asyncio.to_thread(write_audit_started_from_block, ...)` call. Drop the `hooks={...}` block from `ClaudeAgentOptions`. Drop the imports of `make_pre_tool_hook` / `make_post_tool_hook` / `get_hook_callback_failure_count`. Replace the per-project `hook_cb_failures` baseline + `hook_failures` webhook with deletion. |
| `agent/conflict_resolver/sdk_runner.py` | edit | Drop the `hooks={...}` block + the `make_pre_tool_hook` / `make_post_tool_hook` imports. |
| `dashboard/backend/app/routers/webhook.py` | edit | Delete the `hook_failures` event handler at line 126. |
| `dashboard/backend/tests/test_webhook_hook_failures.py` | delete | Tests for a now-removed event handler. |
| `dashboard/backend/tests/test_audit_inline_writes.py` | new | Cover the new stream-derived audit path end-to-end. |
| `dashboard/backend/tests/test_audit_hook.py` | edit | Drop tests of the deleted hook factories (the file likely exists; if it does not, ignore). |

---

## Setup (run once per execution session)

### Task 0: Verify dependency and sync

- [ ] **Step 1: Pull latest dev and verify #384 is merged**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git checkout dev && git pull --ff-only origin dev && \
  grep -n "ClaudeSDKClient" agent/station_orchestrator.py | head -5
```

Expected: at least one `ClaudeSDKClient` reference in the orchestrator. If absent, STOP.

- [ ] **Step 2: Identify the stream-iteration loop in the orchestrator**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "receive_response\|async for message in" agent/station_orchestrator.py
```

Expected: an `async for message in client.receive_response()` (or similar) loop introduced by #384. The exact form depends on the #384 PR; this plan's hooks attach at that loop. Note the line number — we reference it as **STREAM_LOOP** below.

- [ ] **Step 3: Confirm baseline tests pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_hook.py \
                    dashboard/backend/tests/test_webhook_hook_failures.py \
                    dashboard/backend/tests/test_orchestrator_wiring.py -q 2>&1 | tail -15
```

Expected: green.

- [ ] **Step 4: Create the feature branch**

```bash
cd /home/simon/Documents/claude-agent-station && git checkout -b feature/389-inline-audit-from-stream
```

---

## Task 1: New stream-derived audit writers

**Files:**
- Test: `dashboard/backend/tests/test_audit_inline_writes.py` (new)
- Implementation: `agent/audit_hook.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_audit_inline_writes.py`:

```python
"""Tests for the stream-derived audit writers (#389).

After #389, audit_log rows are written directly from ToolUseBlock /
ToolResultBlock items in the orchestrator's stream loop, not from SDK
hook callbacks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """Create a minimal audit_log schema for these tests."""
    path = tmp_path / "station.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            trace_id TEXT,
            run_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            action_detail TEXT,
            status TEXT NOT NULL,
            exit_code INTEGER,
            stdout_tail TEXT,
            stderr_tail TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return str(path)


class _FakeToolUseBlock:
    """Stand-in for claude_agent_sdk.types.ToolUseBlock."""

    def __init__(self, tool_use_id: str, name: str, input_: dict):
        self.id = tool_use_id
        self.name = name
        self.input = input_


class _FakeToolResultBlock:
    """Stand-in for claude_agent_sdk.types.ToolResultBlock."""

    def __init__(self, tool_use_id: str, content, is_error: bool = False):
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


def test_write_audit_started_from_block_inserts_started_row(fresh_db):
    from agent.audit_hook import write_audit_started_from_block

    block = _FakeToolUseBlock(
        tool_use_id="toolu_abc123",
        name="Bash",
        input_={"command": "echo hi"},
    )
    write_audit_started_from_block(
        run_id="run-test",
        actor="lead",
        block=block,
        trace_id="run-test",
        db_path=fresh_db,
    )

    conn = sqlite3.connect(fresh_db)
    row = conn.execute(
        "SELECT idempotency_key, run_id, actor, action_kind, status, finished_at "
        "FROM audit_log WHERE idempotency_key = ?",
        ("toolu_abc123",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "toolu_abc123"
    assert row[1] == "run-test"
    assert row[2] == "lead"
    assert row[3] == "tool.bash"
    assert row[4] == "started"
    assert row[5] is None  # not finished yet


def test_write_audit_finished_from_block_updates_to_ok(fresh_db):
    from agent.audit_hook import (
        write_audit_finished_from_block,
        write_audit_started_from_block,
    )

    write_audit_started_from_block(
        run_id="run-test",
        actor="lead",
        block=_FakeToolUseBlock("toolu_ok", "Read", {"file_path": "/x"}),
        db_path=fresh_db,
    )
    result_block = _FakeToolResultBlock(
        tool_use_id="toolu_ok",
        content="file contents here",
        is_error=False,
    )
    write_audit_finished_from_block(block=result_block, db_path=fresh_db)

    conn = sqlite3.connect(fresh_db)
    row = conn.execute(
        "SELECT status, finished_at, stdout_tail, stderr_tail "
        "FROM audit_log WHERE idempotency_key = ?",
        ("toolu_ok",),
    ).fetchone()
    conn.close()
    assert row[0] == "ok"
    assert row[1] is not None
    assert "file contents here" in (row[2] or "")
    assert row[3] is None


def test_write_audit_finished_from_block_marks_error_on_is_error(fresh_db):
    from agent.audit_hook import (
        write_audit_finished_from_block,
        write_audit_started_from_block,
    )

    write_audit_started_from_block(
        run_id="run-test",
        actor="lead",
        block=_FakeToolUseBlock("toolu_err", "Bash", {"command": "false"}),
        db_path=fresh_db,
    )
    result_block = _FakeToolResultBlock(
        tool_use_id="toolu_err",
        content="permission denied",
        is_error=True,
    )
    write_audit_finished_from_block(block=result_block, db_path=fresh_db)

    conn = sqlite3.connect(fresh_db)
    row = conn.execute(
        "SELECT status, stdout_tail, stderr_tail "
        "FROM audit_log WHERE idempotency_key = ?",
        ("toolu_err",),
    ).fetchone()
    conn.close()
    assert row[0] == "error"
    # Error content goes into stderr_tail when is_error=True; the start
    # row guarantees stdout_tail is populated by the started_at path.
    combined = (row[1] or "") + (row[2] or "")
    assert "permission denied" in combined
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -q
```

Expected: `ImportError` on `write_audit_started_from_block`.

- [ ] **Step 3: Implement the wrappers**

Edit `agent/audit_hook.py`. Add (above the existing `# --- SDK PreToolUse / PostToolUse hook factories` divider):

```python
# --- Stream-derived audit writers (issue #389) -----------------------------


def write_audit_started_from_block(
    *,
    run_id: str,
    actor: str,
    block,                  # claude_agent_sdk.types.ToolUseBlock (or stand-in)
    trace_id: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert a ``status='started'`` audit_log row from a ToolUseBlock.

    Mirrors :func:`write_audit_start` but accepts an already-parsed
    block instead of the SDK's hook-callback dict. Never raises.
    """
    tool_use_id = getattr(block, "id", None) or getattr(block, "tool_use_id", None)
    if not tool_use_id:
        logger.warning("audit_hook: ToolUseBlock missing id; skipping start row")
        return
    tool_name = getattr(block, "name", "") or ""
    tool_input = getattr(block, "input", None) or {}
    write_audit_start(
        idempotency_key=str(tool_use_id),
        run_id=run_id,
        actor=actor,
        tool_name=str(tool_name),
        tool_input=tool_input if isinstance(tool_input, dict) else {"value": tool_input},
        trace_id=trace_id,
        db_path=db_path,
    )


def write_audit_finished_from_block(
    *,
    block,                  # claude_agent_sdk.types.ToolResultBlock (or stand-in)
    db_path: str | None = None,
) -> None:
    """Update the audit_log row keyed by ``block.tool_use_id`` with the
    terminal status drawn from ``block.content`` and ``block.is_error``.

    Mirrors :func:`write_audit_finish` but consumes an SDK
    ``ToolResultBlock`` directly. Never raises.
    """
    tool_use_id = getattr(block, "tool_use_id", None) or getattr(block, "id", None)
    if not tool_use_id:
        logger.warning("audit_hook: ToolResultBlock missing tool_use_id; skipping finish row")
        return
    # _extract_outcome expects the same shape as the SDK's hook
    # ``tool_response`` dict: ``{is_error, exit_code, stdout, stderr, output}``.
    # Build that shape from the block. The block's ``content`` is either a
    # string (Bash output, Read result) or a list of structured items —
    # _coerce_tail handles both.
    content = getattr(block, "content", None)
    is_error = bool(getattr(block, "is_error", False))
    fake_response = {
        "is_error": is_error,
        "output": content,
    }
    if is_error:
        fake_response["stderr"] = content
        fake_response["output"] = None
    write_audit_finish(
        idempotency_key=str(tool_use_id),
        tool_response=fake_response,
        db_path=db_path,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/audit_hook.py dashboard/backend/tests/test_audit_inline_writes.py && \
  git commit -m "feat(audit): add stream-derived audit writers (#389)"
```

---

## Task 2: Sub-agent attribution

The hook callback labels the row's `actor` as `teammate-<sub_agent_id>` when the SDK populates `agent_id` on the callback input. The stream-derived path needs an equivalent. The SDK exposes sub-agent attribution on the message itself via `parent_tool_use_id` (a non-null value means the message originated inside a sub-agent / teammate).

**Files:**
- Test: `dashboard/backend/tests/test_audit_inline_writes.py` (append)
- Implementation: `agent/audit_hook.py` — extend `write_audit_started_from_block` with an explicit `actor` parameter that the orchestrator computes per message. No SDK-internal introspection inside the writer.

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_audit_inline_writes.py`:

```python
def test_write_audit_started_uses_explicit_actor_for_subagents(fresh_db):
    """Caller decides the actor string; the writer trusts it verbatim.

    The orchestrator computes ``teammate-<agent_id>`` from the message's
    ``parent_tool_use_id`` / agent attribution and passes the result as
    ``actor=``. The writer must not introspect the block to override.
    """
    from agent.audit_hook import write_audit_started_from_block

    write_audit_started_from_block(
        run_id="run-test",
        actor="teammate-backend-7",
        block=_FakeToolUseBlock("toolu_sub", "Edit", {"file_path": "/x"}),
        db_path=fresh_db,
    )

    conn = sqlite3.connect(fresh_db)
    row = conn.execute(
        "SELECT actor FROM audit_log WHERE idempotency_key = ?",
        ("toolu_sub",),
    ).fetchone()
    conn.close()
    assert row[0] == "teammate-backend-7"
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py::test_write_audit_started_uses_explicit_actor_for_subagents -q
```

Expected: passes (the writer in Task 1 already accepts `actor` verbatim).

- [ ] **Step 3: No implementation change required**

The contract is: orchestrator computes the actor; writer trusts it.

- [ ] **Step 4: Confirm the broader suite still green**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -v
```

Expected: 4 tests, all passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_audit_inline_writes.py && \
  git commit -m "test(audit): pin actor-attribution contract for inline writes"
```

---

## Task 3: `handle_stream_event` becomes async + writes audit_log for `ToolUseBlock`

**Files:**
- Test: `dashboard/backend/tests/test_audit_inline_writes.py` (append)
- Implementation: `agent/station_orchestrator.py`

- [ ] **Step 1: Write the failing integration test**

Append to `dashboard/backend/tests/test_audit_inline_writes.py`:

```python
@pytest.mark.asyncio
async def test_handle_stream_event_writes_audit_for_tooluseblock(fresh_db, monkeypatch):
    """Five ToolUseBlocks → five 'started' audit_log rows."""
    monkeypatch.setenv("STATION_DB_PATH", fresh_db)

    from agent import station_orchestrator as so
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    # Build five fake tool calls inside one AssistantMessage.
    blocks = [
        ToolUseBlock(id=f"toolu_{i}", name="Bash", input={"command": f"echo {i}"})
        for i in range(5)
    ]
    msg = AssistantMessage(
        content=blocks,
        model="claude-opus-4-7",
    )
    # AssistantMessage may have a usage attribute; set to empty if needed.
    try:
        msg.usage = {"input_tokens": 0, "output_tokens": 0}
    except AttributeError:
        pass

    state = so._StreamState()
    config = {"webhook_url": ""}  # post_webhook will no-op with empty URL

    # handle_stream_event is async after #389.
    await so.handle_stream_event(msg, config, "test", log_file=None, state=state)

    conn = sqlite3.connect(fresh_db)
    rows = conn.execute(
        "SELECT idempotency_key, status FROM audit_log ORDER BY idempotency_key"
    ).fetchall()
    conn.close()
    assert len(rows) == 5
    for i, (key, status) in enumerate(rows):
        assert key == f"toolu_{i}"
        assert status == "started"
```

Note: the exact `AssistantMessage` / `ToolUseBlock` constructor signatures come from `claude_agent_sdk.types`. If your local SDK version differs, adapt the field names (`id` vs `tool_use_id`, `input` vs `arguments`). The orchestrator already imports these types from the SDK at `agent/station_orchestrator.py:32-43`, so any constructor mismatch surfaces in the orchestrator import too.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py::test_handle_stream_event_writes_audit_for_tooluseblock -q
```

Expected: FAILED — `handle_stream_event` is still synchronous OR it does not yet write to `audit_log`.

- [ ] **Step 3: Update `handle_stream_event` + the caller**

Edit `agent/station_orchestrator.py`:

First, add the new writer import to the top-of-file `from agent.audit_hook import (...)` block:

```python
from agent.audit_hook import (
    make_audited_policy,
    write_audit_finished_from_block,
    write_audit_started_from_block,
)
```

Remove the old import lines for `get_hook_callback_failure_count`, `make_post_tool_hook`, `make_pre_tool_hook` from the same block.

Add `import asyncio` to the top of the file if it is not already imported (it is, but verify with `grep ^import agent/station_orchestrator.py`).

Add `UserMessage` to the SDK types import:

```python
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
    ToolResultBlock,
    UserMessage,
)
```

Change the signature of `handle_stream_event` (around line 1314):

```python
async def handle_stream_event(
    message, config: dict, run_id: str, log_file=None, state: _StreamState | None = None,
) -> None:
    """Forward SDK stream messages to the dashboard and write to log file.

    Async after #389: the stream-derived audit writer offloads sqlite3
    via ``asyncio.to_thread``.
    """
```

Inside the `if isinstance(message, AssistantMessage):` branch, find the `elif isinstance(block, ToolUseBlock):` arm (around line 1354) and extend it:

```python
                elif isinstance(block, ToolUseBlock):
                    if state:
                        state.tool_calls += 1
                    logger.info("Lead agent tool call: %s", block.name)
                    # #389: write audit_log row inline from the block,
                    # not from a separate SDK hook callback. Off-load
                    # sqlite3 so the stream loop is not blocked.
                    actor = _actor_for_message(message, default="lead")
                    await asyncio.to_thread(
                        write_audit_started_from_block,
                        run_id=f"run-{run_id}",
                        actor=actor,
                        block=block,
                        trace_id=f"run-{run_id}",
                    )
                    if pending_narration:
                        post_webhook(config, "narration", {
                            "run_id": f"run-{run_id}",
                            "agent_name": "Lead",
                            "narration": pending_narration[:500],
                            "narration_kind": "directive",
                        })
                        pending_narration = None
```

Add a `UserMessage` branch after the `AssistantMessage` block (before the `TaskStartedMessage` branch around line 1405):

```python
    elif isinstance(message, UserMessage):
        # #389: tool results arrive as ToolResultBlock items inside
        # UserMessage.content. Walk them and write the matching audit_log
        # finish row.
        content = message.content if isinstance(message.content, list) else [message.content]
        for block in content:
            if isinstance(block, ToolResultBlock):
                await asyncio.to_thread(
                    write_audit_finished_from_block,
                    block=block,
                )
```

Add the `_actor_for_message` helper near `_usage_val` (around line 1305):

```python
def _actor_for_message(message, *, default: str = "lead") -> str:
    """Compute the audit_log ``actor`` for an AssistantMessage.

    SDK populates ``parent_tool_use_id`` on messages that originate
    inside a sub-agent / Agent Teams teammate. When present, prefix
    with ``teammate-`` so the audit timeline can distinguish lead vs
    teammate work. Falls back to ``default`` for main-thread messages.
    """
    parent = getattr(message, "parent_tool_use_id", None)
    if parent:
        # The SDK exposes the sub-agent's identifier on a sibling
        # attribute (``agent_id`` or ``sub_agent_id`` depending on
        # version). Try both; fall back to the parent_tool_use_id
        # itself as a stable identifier.
        sub_id = (
            getattr(message, "agent_id", None)
            or getattr(message, "sub_agent_id", None)
            or parent
        )
        return f"teammate-{sub_id}"
    return default
```

Find the caller at line 2079 (`handle_stream_event(message, config, run_id, log_file=log_file, state=stream_state)`) — after the #384 migration this is likely `await handle_stream_event(...)` already; if not, add the `await`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -v
```

Expected: 5 tests, all passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/station_orchestrator.py dashboard/backend/tests/test_audit_inline_writes.py && \
  git commit -m "feat(audit): write audit_log inline from stream (#389)"
```

---

## Task 4: Walk `ToolResultBlock`s in `UserMessage` to finish the rows

This task is partly covered by Task 3. The dedicated test below pins the start → finish flow as one assertion.

**Files:**
- Test: `dashboard/backend/tests/test_audit_inline_writes.py` (append)
- Implementation: already done in Task 3

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_audit_inline_writes.py`:

```python
@pytest.mark.asyncio
async def test_handle_stream_event_completes_rows_on_userresult(fresh_db, monkeypatch):
    """Started rows transition to ok/error when the ToolResultBlock arrives."""
    monkeypatch.setenv("STATION_DB_PATH", fresh_db)

    from agent import station_orchestrator as so
    from claude_agent_sdk.types import (
        AssistantMessage,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    use_blocks = [
        ToolUseBlock(id="toolu_a", name="Bash", input={"command": "echo a"}),
        ToolUseBlock(id="toolu_b", name="Read", input={"file_path": "/x"}),
    ]
    asst = AssistantMessage(content=use_blocks, model="claude-opus-4-7")
    try:
        asst.usage = {"input_tokens": 0, "output_tokens": 0}
    except AttributeError:
        pass

    state = so._StreamState()
    await so.handle_stream_event(asst, {"webhook_url": ""}, "test", state=state)

    # Result message: one ok, one error.
    result_blocks = [
        ToolResultBlock(tool_use_id="toolu_a", content="a\n", is_error=False),
        ToolResultBlock(tool_use_id="toolu_b", content="EACCES", is_error=True),
    ]
    user = UserMessage(content=result_blocks)
    await so.handle_stream_event(user, {"webhook_url": ""}, "test", state=state)

    conn = sqlite3.connect(fresh_db)
    rows = dict(conn.execute(
        "SELECT idempotency_key, status FROM audit_log"
    ).fetchall())
    conn.close()
    assert rows == {"toolu_a": "ok", "toolu_b": "error"}
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py::test_handle_stream_event_completes_rows_on_userresult -q
```

Expected: `1 passed` (Task 3 already wired the UserMessage branch).

- [ ] **Step 3: No implementation change required**

If the test fails, audit the `UserMessage` branch in Task 3 and adjust the ToolResultBlock walking.

- [ ] **Step 4: Confirm broader suite**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -v
```

Expected: 6 tests, all passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_audit_inline_writes.py && \
  git commit -m "test(audit): pin started→ok / started→error transitions"
```

---

## Task 5: Delete the hook factories and ClaudeAgentOptions wiring

**Files:**
- Implementation: `agent/audit_hook.py`, `agent/station_orchestrator.py`, `agent/conflict_resolver/sdk_runner.py`
- Test: `dashboard/backend/tests/test_audit_inline_writes.py` (append a grep guard)

- [ ] **Step 1: Write the failing grep guard test**

Append to `dashboard/backend/tests/test_audit_inline_writes.py`:

```python
def test_pre_post_tool_hook_factories_are_gone():
    """#389 acceptance: ``make_pre_tool_hook`` / ``make_post_tool_hook`` are
    deleted from audit_hook and not imported anywhere under agent/.
    """
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["grep", "-rn", "make_pre_tool_hook\\|make_post_tool_hook\\|PreToolUse\\|PostToolUse", "agent/"],
        cwd=repo, capture_output=True, text=True,
    )
    hits = [l for l in result.stdout.splitlines() if l]
    # The audit_hook module itself MAY retain a one-line docstring or
    # migration note that mentions the old names — but nothing imports
    # or registers them.
    forbidden_substrings = ("make_pre_tool_hook(", "make_post_tool_hook(", "HookMatcher(hooks=")
    for line in hits:
        for sub in forbidden_substrings:
            assert sub not in line, f"orphan reference: {line}"


def test_hook_callback_failure_count_helper_is_gone():
    """get_hook_callback_failure_count and the counter are deleted (#389)."""
    from agent import audit_hook
    assert not hasattr(audit_hook, "get_hook_callback_failure_count")
    assert not hasattr(audit_hook, "_HOOK_CB_FAILURE_COUNT")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py::test_pre_post_tool_hook_factories_are_gone \
                    dashboard/backend/tests/test_audit_inline_writes.py::test_hook_callback_failure_count_helper_is_gone -q
```

Expected: FAILED — the factories still exist.

- [ ] **Step 3: Delete the factories and counter from `agent/audit_hook.py`**

Delete:

- The `_HOOK_CB_FAILURE_COUNT = 0` global.
- The `get_hook_callback_failure_count` function.
- The `_record_hook_failure` function.
- The `make_pre_tool_hook` factory.
- The `make_post_tool_hook` factory.
- The "# --- SDK PreToolUse / PostToolUse hook factories ---" section heading.

Keep:

- `make_audited_policy` (still needed for `can_use_tool`).
- `write_audit_start` / `write_audit_finish` (internal writers; the new stream wrappers delegate to them).
- `write_decision_event`.
- The new `write_audit_started_from_block` / `write_audit_finished_from_block`.

- [ ] **Step 4: Drop the `hooks={...}` block + import from the orchestrator**

In `agent/station_orchestrator.py`:

Find the `ClaudeAgentOptions(...)` construction around line 2018 (the `make_audited_policy` call) and delete the `hooks={...}` block (lines 2026-2040):

```python
                        # Issue #73: per-tool-call audit_log telemetry.
                        # Pre-hook writes a 'started' row keyed by SDK tool_use_id;
                        # Post-hook updates the same row with status + tails.
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
```

Remove the `HookMatcher` import from the `from claude_agent_sdk.types import (...)` block if no other reference remains:

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "HookMatcher" agent/station_orchestrator.py
```

Expected after deletion: zero matches (or one match in a comment, which is fine but not necessary).

Delete the per-project `hook_cb_failures` accounting in the `finally` block (around lines 2157–2174):

```python
            # Surface hook-callback failures for this project's session.
            ...
            hook_cb_failures = get_hook_callback_failure_count() - hook_cb_failures_baseline
            if hook_cb_failures > 0:
                logger.warning(...)
                post_webhook(config, "hook_failures", {...})
```

Also delete the baseline capture at line 1739:

```python
        hook_cb_failures_baseline = get_hook_callback_failure_count()
```

- [ ] **Step 5: Drop the `hooks={...}` block + imports from `agent/conflict_resolver/sdk_runner.py`**

Delete the `hooks={...}` block (around lines 64-78) and remove `make_pre_tool_hook` / `make_post_tool_hook` from the imports at the top (lines 22-23). Remove `HookMatcher` from the SDK types import (line 18) if no other usage remains:

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "HookMatcher\|make_pre_tool_hook\|make_post_tool_hook" agent/conflict_resolver/sdk_runner.py
```

Expected after deletion: zero matches.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_audit_inline_writes.py -v
```

Expected: all 8 tests passing.

- [ ] **Step 7: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/audit_hook.py agent/station_orchestrator.py \
          agent/conflict_resolver/sdk_runner.py \
          dashboard/backend/tests/test_audit_inline_writes.py && \
  git commit -m "chore(audit): delete PreToolUse/PostToolUse hook factories (#389)"
```

---

## Task 6: Delete the `hook_failures` webhook handler

**Files:**
- Implementation: `dashboard/backend/app/routers/webhook.py`
- Test: `dashboard/backend/tests/test_webhook_hook_failures.py` (delete)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_webhook_hook_failures_removed.py`:

```python
"""Pin that the hook_failures webhook event is no longer handled (#389)."""

import inspect

from app.routers import webhook


def test_hook_failures_event_is_no_longer_handled():
    src = inspect.getsource(webhook)
    # The router must no longer have an "elif event_name == 'hook_failures'"
    # branch (or a `hook_failures` string compare).
    assert "hook_failures" not in src, (
        "webhook router still references the hook_failures event; #389 "
        "deleted the inline audit hook so this event no longer fires."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_webhook_hook_failures_removed.py -q
```

Expected: FAILED.

- [ ] **Step 3: Delete the `hook_failures` branch and the old test file**

Edit `dashboard/backend/app/routers/webhook.py`. Delete the `elif event_name == "hook_failures":` branch (line 126 and its body — read the surrounding 15 lines to determine the body's extent; typically 5-15 lines of `agent_event` insertion logic).

Delete `dashboard/backend/tests/test_webhook_hook_failures.py` entirely:

```bash
cd /home/simon/Documents/claude-agent-station && \
  git rm dashboard/backend/tests/test_webhook_hook_failures.py
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_webhook_hook_failures_removed.py dashboard/backend/tests/ -q 2>&1 | tail -20
```

Expected: green; the removal pin passes and no other test references `hook_failures`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/app/routers/webhook.py \
          dashboard/backend/tests/test_webhook_hook_failures_removed.py && \
  git commit -m "chore(webhook): drop hook_failures handler (dead after #389)"
```

---

## Task 7: Frontend cleanup (if needed)

**Files:**
- Implementation: `dashboard/frontend/src/lib/event-stream.ts` and any consumer rendering the `hook_failures` event distinctly.

- [ ] **Step 1: Check whether the frontend still references the event**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -rn "hook_failures\|hook-cb-fail\|hook_cb_fail" dashboard/frontend/src 2>&1
```

If zero matches, skip to Task 8.

- [ ] **Step 2: Write a presence-removal test**

Create `dashboard/frontend/tests/event-stream-cleanup.spec.ts` (or whichever test framework the frontend already uses; if there's no existing test harness, use a grep test inside the backend pytest suite):

If the frontend has no test harness, fall back to a pytest grep guard in `dashboard/backend/tests/test_frontend_grep_389.py`:

```python
"""Pin that the frontend no longer renders the hook_failures event (#389)."""

import subprocess
from pathlib import Path


def test_frontend_does_not_reference_hook_failures():
    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["grep", "-rn", "hook_failures\\|hook-cb-fail", "dashboard/frontend/src"],
        cwd=repo, capture_output=True, text=True,
    )
    hits = [l for l in result.stdout.splitlines() if l]
    assert hits == [], f"orphan frontend references: {hits}"
```

- [ ] **Step 3: Run to verify it fails (or passes immediately)**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_frontend_grep_389.py -q
```

If it passes, skip Step 4.

- [ ] **Step 4: Remove frontend references**

Open the matching files and delete the `hook_failures` switch arm, the event type from `Verdict | RunEvent | ...` unions in `types.ts`, and any UI render path.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/frontend/src/ dashboard/backend/tests/test_frontend_grep_389.py && \
  git commit -m "chore(frontend): drop hook_failures event handling (dead after #389)"
```

---

## Task 8: End-to-end + PR

- [ ] **Step 1: Run the full pytest suite**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/ -q 2>&1 | tail -30
```

Expected: all green.

- [ ] **Step 2: Verify grep contracts hold**

```bash
cd /home/simon/Documents/claude-agent-station && \
  echo "--- PreToolUse/PostToolUse in agent/ ---" && \
  grep -rn "PreToolUse\|PostToolUse" agent/ ; \
  echo "--- hook factories ---" && \
  grep -rn "make_pre_tool_hook\|make_post_tool_hook" agent/ ; \
  echo "--- hook_failures references ---" && \
  grep -rn "hook_failures" dashboard/backend/app/ dashboard/frontend/src/
```

Expected: each `grep` returns no matches.

- [ ] **Step 3: Frontend build smoke**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds.

- [ ] **Step 4: Push and open the PR**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git push -u origin feature/389-inline-audit-from-stream && \
  gh pr create --base dev --head feature/389-inline-audit-from-stream \
    --title "feat(audit): write audit_log inline from stream (#389)" \
    --body "$(cat <<'EOF'
## Summary
- Add `write_audit_started_from_block` / `write_audit_finished_from_block` that consume SDK `ToolUseBlock` / `ToolResultBlock` items and delegate to the existing sqlite3 writers.
- Make `handle_stream_event` async; walk `ToolUseBlock` items in `AssistantMessage.content` and `ToolResultBlock` items in `UserMessage.content`, offloading sqlite3 via `asyncio.to_thread`.
- Delete `make_pre_tool_hook` / `make_post_tool_hook`, `_record_hook_failure`, `_HOOK_CB_FAILURE_COUNT`, `get_hook_callback_failure_count`, and the `hooks={"PreToolUse": ..., "PostToolUse": ...}` block from both `ClaudeAgentOptions` call sites.
- Delete the `hook_failures` webhook handler and its tests.
- `can_use_tool` callback (autonomy + `auto_mode_decision` rows) is untouched — different SDK path.

## Test plan
- [x] `pytest dashboard/backend/tests/test_audit_inline_writes.py` (8 tests green: started rows, finish transitions, sub-agent attribution, hook factory removal pin)
- [x] `pytest dashboard/backend/tests/test_webhook_hook_failures_removed.py`
- [x] `grep -rn "PreToolUse|PostToolUse" agent/` returns empty
- [x] `npm run build` in `dashboard/frontend` (no TypeScript errors)
- [ ] Manual: trigger a full Agent Teams run on the dev box; `SELECT COUNT(*) FROM audit_log WHERE run_id = ? AND finished_at IS NULL` returns 0; no `[hook-cb-fail]` warnings in launcher.out.

Closes #389
Depends on #384
EOF
)"
```

- [ ] **Step 5: Manual production-shape validation (dev box)**

After CI green and merge, on the dev box:

```bash
# After a live run completes:
sqlite3 /var/lib/claude-agent-station/station.db \
  "SELECT actor, COUNT(*) FROM audit_log WHERE run_id = 'run-<id>' GROUP BY actor"
```

Expected: rows for `lead` and any `teammate-*` siblings. No rows with `status='started'` and `finished_at IS NULL` after the run finishes.

```bash
sudo grep -c '\[hook-cb-fail\]' /var/log/claude-agent/run-*-launcher.out
```

Expected: `0` (the warning prefix is gone with `_record_hook_failure`).

---

## Acceptance-criteria coverage

| Spec criterion | Tasks |
|---|---|
| `agent/audit_hook.py` deletes the PreToolUse / PostToolUse hook factories | Task 5 (delete) + Task 5 grep-pin test |
| `agent/station_orchestrator.py::handle_stream_event` writes audit_log rows directly from `ToolUseBlock` and tool-result events | Task 3 (start) + Task 4 (finish) |
| `can_use_tool` callback path stays | Tasks 3, 5 (no change to `make_audited_policy`) |
| Test: simulated stream with 5 tool calls → 5 audit_log rows, no SDK hook registration | Task 3, Step 1 (the 5-tool test) + Task 5 (factory absence) |
| Sub-agent attribution preserved | Task 2 (`_actor_for_message`) |
| `[hook-cb-fail]` warning disappears | Task 5 + Task 8 manual validation |
