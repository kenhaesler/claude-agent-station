"""Tests for the audit_log writers in agent/audit_hook.py (issue #73).

Covers:
- write_audit_start inserts a 'started' row keyed by idempotency_key
- Re-calling write_audit_start with the same key is a no-op (INSERT OR IGNORE)
- write_audit_finish updates the row with status/exit_code/tails/finished_at
- _extract_outcome handles dict bash-style responses + non-dict fallback
- Best-effort: writers don't raise when the table or DB is missing
- Pre/Post hook callbacks can be composed end-to-end
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from agent.audit_hook import (
    _extract_outcome,
    make_post_tool_hook,
    make_pre_tool_hook,
    write_audit_finish,
    write_audit_start,
)


def _init_audit_db(path: Path) -> None:
    """Schema mirrors AuditEntry in models.py."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            action_detail TEXT,
            status TEXT NOT NULL,
            exit_code INTEGER,
            stdout_tail TEXT,
            stderr_tail TEXT,
            started_at DATETIME NOT NULL,
            finished_at DATETIME
        )
        """
    )
    conn.commit()
    conn.close()


def _rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM audit_log ORDER BY id"))
    conn.close()
    return rows


# --- write_audit_start -----------------------------------------------------


def test_write_audit_start_inserts_started_row(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    write_audit_start(
        idempotency_key="tu_abc",
        run_id="run-1",
        actor="lead",
        tool_name="Bash",
        tool_input={"command": "ls"},
        trace_id="trace-1",
        db_path=str(db),
    )

    rows = _rows(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["idempotency_key"] == "tu_abc"
    assert r["run_id"] == "run-1"
    assert r["actor"] == "lead"
    assert r["action_kind"] == "tool.bash"
    assert r["status"] == "started"
    assert r["finished_at"] is None
    detail = json.loads(r["action_detail"])
    assert detail["tool_name"] == "Bash"
    assert detail["tool_input"] == {"command": "ls"}


def test_write_audit_start_is_idempotent_on_duplicate_key(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    for _ in range(3):
        write_audit_start(
            idempotency_key="tu_dup",
            run_id="run-1",
            actor="lead",
            tool_name="Read",
            tool_input={"file_path": "/etc/hosts"},
            db_path=str(db),
        )

    rows = _rows(db)
    assert len(rows) == 1


def test_write_audit_start_does_not_raise_when_table_missing(tmp_path):
    db = tmp_path / "broken.db"
    sqlite3.connect(str(db)).close()  # empty DB, no schema

    write_audit_start(
        idempotency_key="tu_x",
        run_id="run-x",
        actor="lead",
        tool_name="Read",
        tool_input={},
        db_path=str(db),
    )


# --- write_audit_finish ----------------------------------------------------


def test_write_audit_finish_updates_status_and_tails(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    write_audit_start(
        idempotency_key="tu_fin",
        run_id="run-1",
        actor="lead",
        tool_name="Bash",
        tool_input={"command": "echo hi"},
        db_path=str(db),
    )

    write_audit_finish(
        idempotency_key="tu_fin",
        tool_response={"stdout": "hi\n", "stderr": "", "exit_code": 0, "is_error": False},
        db_path=str(db),
    )

    rows = _rows(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "ok"
    assert r["exit_code"] == 0
    assert "hi" in r["stdout_tail"]
    assert r["finished_at"] is not None


def test_write_audit_finish_marks_error_on_nonzero_exit(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    write_audit_start(
        idempotency_key="tu_err",
        run_id="run-1",
        actor="lead",
        tool_name="Bash",
        tool_input={"command": "false"},
        db_path=str(db),
    )
    write_audit_finish(
        idempotency_key="tu_err",
        tool_response={"stdout": "", "stderr": "boom", "exit_code": 2, "is_error": True},
        db_path=str(db),
    )

    rows = _rows(db)
    assert rows[0]["status"] == "error"
    assert rows[0]["exit_code"] == 2
    assert "boom" in rows[0]["stderr_tail"]


def test_write_audit_finish_is_noop_when_row_missing(tmp_path):
    """No matching idempotency_key → silent no-op (best-effort)."""
    db = tmp_path / "test.db"
    _init_audit_db(db)

    write_audit_finish(
        idempotency_key="tu_orphan",
        tool_response={"stdout": "x"},
        db_path=str(db),
    )
    assert _rows(db) == []


# --- _extract_outcome ------------------------------------------------------


def test_extract_outcome_handles_bash_dict():
    s, ec, out, err = _extract_outcome(
        {"stdout": "ok", "stderr": "", "exit_code": 0, "is_error": False}
    )
    assert s == "ok"
    assert ec == 0
    assert out == "ok"
    assert err == ""


def test_extract_outcome_preserves_explicit_empty_stdout():
    """An explicit ``stdout=""`` must not fall through to ``output``."""
    s, ec, out, err = _extract_outcome(
        {"stdout": "", "output": "should-not-be-used", "exit_code": 0, "is_error": False}
    )
    assert s == "ok"
    assert ec == 0
    assert out == ""
    assert err is None


def test_extract_outcome_handles_non_dict_string():
    s, ec, out, err = _extract_outcome("file contents…")
    assert s == "ok"
    assert ec is None
    assert out == "file contents…"
    assert err is None


def test_extract_outcome_truncates_long_strings():
    huge = "x" * 100_000
    _, _, out, _ = _extract_outcome({"stdout": huge})
    assert out is not None
    # Truncated to TAIL_LIMIT + the truncation marker
    assert len(out) < 10_000
    assert "+" in out  # marker like "[+96000 chars]"


# --- pre/post hook callbacks ----------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_pre_then_post_hooks_compose_end_to_end(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    pre = make_pre_tool_hook(run_id="run-h", actor="lead", trace_id="trace-h", db_path=str(db))
    post = make_post_tool_hook(run_id="run-h", actor="lead", db_path=str(db))

    pre_input = {
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_use_id": "tu_hook",
    }
    post_input = {
        "tool_use_id": "tu_hook",
        "tool_response": {"stdout": "ok", "exit_code": 0, "is_error": False},
    }

    asyncio.run(pre(pre_input, None, {"signal": None}))
    asyncio.run(post(post_input, None, {"signal": None}))

    rows = _rows(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "ok"
    assert r["action_kind"] == "tool.bash"
    assert r["trace_id"] == "trace-h"
    assert r["finished_at"] is not None


def test_pre_hook_attributes_to_teammate_when_agent_id_present(tmp_path):
    """SDK populates agent_id on hook inputs from sub-agents — use it for actor."""
    db = tmp_path / "test.db"
    _init_audit_db(db)

    pre = make_pre_tool_hook(run_id="run-h", actor="lead", db_path=str(db))
    asyncio.run(pre(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            "tool_use_id": "tu_team",
            "agent_id": "issue-worker",
        },
        None,
        {"signal": None},
    ))

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["actor"] == "teammate-issue-worker"


def test_pre_hook_falls_back_to_lead_when_agent_id_absent(tmp_path):
    """Main-thread tool calls (no agent_id) keep the configured actor."""
    db = tmp_path / "test.db"
    _init_audit_db(db)

    pre = make_pre_tool_hook(run_id="run-h", actor="lead", db_path=str(db))
    asyncio.run(pre(
        {"tool_name": "Bash", "tool_input": {}, "tool_use_id": "tu_lead"},
        None,
        {"signal": None},
    ))

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["actor"] == "lead"


def test_hooks_are_silent_when_tool_use_id_missing(tmp_path):
    db = tmp_path / "test.db"
    _init_audit_db(db)

    pre = make_pre_tool_hook(run_id="run-h", db_path=str(db))
    asyncio.run(pre({"tool_name": "Read", "tool_input": {}}, None, {"signal": None}))

    assert _rows(db) == []
