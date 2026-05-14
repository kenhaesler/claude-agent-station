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
