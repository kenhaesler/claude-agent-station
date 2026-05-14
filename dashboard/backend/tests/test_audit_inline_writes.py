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
