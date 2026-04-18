"""Unit tests for the Auto Mode audit hook — ADR-0001.

Covers:
- make_audited_policy composes policy_decide with a DB write per call.
- Allow/Deny outcomes are labelled correctly in the event row.
- Long string inputs are truncated so we don't store file bodies.
- DB write failures don't propagate (best-effort logging).
- Always-deny path still records an event with the deny reason.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.audit_hook import (
    EVENT_TYPE,
    _summarise_input,
    make_audited_policy,
    write_decision_event,
)
from agent.auto_mode import AutonomyLevel
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def _init_db(path: Path) -> None:
    """Create a minimal agent_events table matching models.py:227."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE agent_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            run_id TEXT,
            agent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            team_name TEXT,
            event_data TEXT NOT NULL,
            parent_event_id INTEGER,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _fetch_events(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM agent_events ORDER BY event_id"))
    conn.close()
    return rows


# --- write_decision_event --------------------------------------------------


def test_write_decision_event_inserts_allow_row(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    write_decision_event(
        run_id="run-001",
        agent_id="lead",
        level=AutonomyLevel.ASSISTED,
        tool_name="Read",
        tool_input={"file_path": "/etc/hosts"},
        decision=PermissionResultAllow(),
        db_path=str(db),
    )

    rows = _fetch_events(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["workflow_id"] == "run-001"
    assert row["run_id"] == "run-001"
    assert row["agent_id"] == "lead"
    assert row["event_type"] == EVENT_TYPE
    payload = json.loads(row["event_data"])
    assert payload["level"] == "assisted"
    assert payload["tool_name"] == "Read"
    assert payload["decision"] == "allow"
    assert payload["reason"] == ""


def test_write_decision_event_inserts_deny_row_with_reason(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    deny = PermissionResultDeny(message="blocked by always-deny policy: push to main")
    write_decision_event(
        run_id="run-042",
        agent_id="lead",
        level=AutonomyLevel.AUTO,
        tool_name="Bash",
        tool_input={"command": "git push origin main"},
        decision=deny,
        db_path=str(db),
    )

    rows = _fetch_events(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["event_data"])
    assert payload["decision"] == "deny"
    assert "push to main" in payload["reason"]


def test_write_decision_event_is_best_effort_on_missing_table(tmp_path):
    """If the DB exists but the table is absent, we must NOT raise."""
    db = tmp_path / "broken.db"
    # Create an empty DB without the agent_events table
    sqlite3.connect(str(db)).close()

    # Should log a warning internally but never raise
    write_decision_event(
        run_id="run-001",
        agent_id="lead",
        level=AutonomyLevel.MANUAL,
        tool_name="Read",
        tool_input={},
        decision=PermissionResultAllow(),
        db_path=str(db),
    )


# --- _summarise_input ------------------------------------------------------


def test_summarise_input_truncates_long_strings():
    huge = "x" * 1000
    summary = _summarise_input({"content": huge, "path": "/tmp/x"})
    assert len(summary["content"]) < 1000
    assert summary["content"].startswith("x" * 500)
    assert "+500 chars" in summary["content"]
    assert summary["path"] == "/tmp/x"


def test_summarise_input_preserves_non_string_values():
    summary = _summarise_input({"count": 42, "flag": True, "items": [1, 2, 3]})
    assert summary == {"count": 42, "flag": True, "items": [1, 2, 3]}


# --- make_audited_policy ---------------------------------------------------


async def test_audited_policy_allows_read_and_records_event(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    can_use_tool = make_audited_policy(
        run_id="run-777",
        level=AutonomyLevel.ASSISTED,
        db_path=str(db),
    )
    result = await can_use_tool("Read", {"file_path": "/etc/hosts"}, None)

    assert isinstance(result, PermissionResultAllow)
    rows = _fetch_events(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["event_data"])
    assert payload["decision"] == "allow"
    assert payload["level"] == "assisted"
    assert payload["tool_name"] == "Read"


async def test_audited_policy_denies_destructive_bash_at_manual(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    can_use_tool = make_audited_policy(
        run_id="run-manual",
        level=AutonomyLevel.MANUAL,
        db_path=str(db),
    )
    result = await can_use_tool("Bash", {"command": "rm -rf node_modules"}, None)

    assert isinstance(result, PermissionResultDeny)
    rows = _fetch_events(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["event_data"])
    assert payload["decision"] == "deny"
    assert payload["level"] == "manual"


async def test_audited_policy_denies_push_to_main_even_at_auto(tmp_path):
    """ALWAYS_DENY beats autonomy level; audit still records the decision."""
    db = tmp_path / "test.db"
    _init_db(db)

    can_use_tool = make_audited_policy(
        run_id="run-auto",
        level=AutonomyLevel.AUTO,
        db_path=str(db),
    )
    result = await can_use_tool("Bash", {"command": "git push origin main"}, None)

    assert isinstance(result, PermissionResultDeny)
    rows = _fetch_events(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["event_data"])
    assert payload["decision"] == "deny"
    assert "push to main" in payload["reason"]


async def test_audited_policy_writes_one_row_per_call(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    can_use_tool = make_audited_policy(
        run_id="run-seq",
        level=AutonomyLevel.AUTO,
        db_path=str(db),
    )
    await can_use_tool("Read", {"file_path": "/a"}, None)
    await can_use_tool("Bash", {"command": "ls"}, None)
    await can_use_tool("Edit", {"file_path": "/a", "content": "x"}, None)

    rows = _fetch_events(db)
    assert len(rows) == 3
    tools = [json.loads(r["event_data"])["tool_name"] for r in rows]
    assert tools == ["Read", "Bash", "Edit"]


async def test_audited_policy_tags_agent_id(tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)

    can_use_tool = make_audited_policy(
        run_id="run-team",
        level=AutonomyLevel.ASSISTED,
        agent_id="teammate-issue-worker",
        db_path=str(db),
    )
    await can_use_tool("Read", {"file_path": "/a"}, None)

    rows = _fetch_events(db)
    assert rows[0]["agent_id"] == "teammate-issue-worker"


async def test_audited_policy_does_not_raise_when_db_missing(tmp_path):
    """Audit is best-effort; a broken DB must not break tool execution."""
    db = tmp_path / "does-not-exist.db"
    # Intentionally do not initialise the schema

    can_use_tool = make_audited_policy(
        run_id="run-nop",
        level=AutonomyLevel.ASSISTED,
        db_path=str(db),
    )
    # Must still return a valid PermissionResult
    result = await can_use_tool("Read", {"file_path": "/a"}, None)
    assert isinstance(result, PermissionResultAllow)
