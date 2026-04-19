"""Tests that the policy engine honors Mission Control pause flags (Phase A).

Verifies:
- A paused run routes non-readonly tool calls to the tray (even at auto level).
- Global pause affects every run regardless of its own pause state.
- Read-only tools still flow through while paused (operators still need visibility).
- With the tray mocked as approving, paused runs continue after approval.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from agent import run_control
from agent.auto_mode import AutonomyLevel, policy_decide
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def _is_allow(result) -> bool:
    return isinstance(result, PermissionResultAllow)


def _is_deny(result) -> bool:
    return isinstance(result, PermissionResultDeny)


@pytest.fixture
def station_db():
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE station_control (
          id INTEGER PRIMARY KEY,
          global_pause INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE run_controls (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT,
          action TEXT,
          payload TEXT,
          requested_by TEXT,
          requested_at TEXT,
          consumed_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO station_control (id, global_pause) VALUES (1, 0)")
    # permission_requests + agent_events are written by the tray / audit hook.
    conn.execute(
        """
        CREATE TABLE permission_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_id TEXT UNIQUE,
          status TEXT,
          resolution_note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE agent_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          workflow_id TEXT, run_id TEXT, agent_id TEXT,
          event_type TEXT, team_name TEXT, event_data TEXT, created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    os.environ["STATION_DB_PATH"] = path
    run_control._paused_runs.clear()
    yield path
    # Clean up module state so tests stay isolated.
    run_control._paused_runs.clear()
    os.unlink(path)


async def test_paused_run_defers_bash(station_db):
    """A run marked paused should route its Bash calls to the tray (approve)."""
    run_control.set_run_paused("run-paused", True)

    async def fake_refer(**kwargs):
        assert kwargs["reason"] == "operator paused this run"
        assert kwargs["run_id"] == "run-paused"
        return PermissionResultAllow()

    with patch("agent.tray_referral.refer_to_operator", side_effect=fake_refer):
        result = await policy_decide(
            "Bash", {"command": "ls -la"}, None,
            AutonomyLevel.AUTO,  # even in AUTO, pause forces the tray
            run_id="run-paused", agent_id="lead",
        )
    assert _is_allow(result)


async def test_paused_run_denied_when_tray_denies(station_db):
    run_control.set_run_paused("run-paused", True)

    async def fake_refer(**kwargs):
        return PermissionResultDeny(message="operator denied")

    with patch("agent.tray_referral.refer_to_operator", side_effect=fake_refer):
        result = await policy_decide(
            "Edit", {"file_path": "/tmp/x"}, None,
            AutonomyLevel.AUTO,
            run_id="run-paused", agent_id="lead",
        )
    assert _is_deny(result)


async def test_global_pause_applies_without_per_run_flag(station_db):
    conn = sqlite3.connect(station_db)
    conn.execute("UPDATE station_control SET global_pause = 1 WHERE id = 1")
    conn.commit()
    conn.close()
    assert run_control.is_global_paused()
    assert not run_control.is_run_paused("some-other-run")

    async def fake_refer(**kwargs):
        assert kwargs["reason"] == "operator triggered global pause"
        return PermissionResultAllow()

    with patch("agent.tray_referral.refer_to_operator", side_effect=fake_refer):
        result = await policy_decide(
            "Edit", {"file_path": "/tmp/x"}, None,
            AutonomyLevel.AUTO,
            run_id="some-other-run", agent_id="lead",
        )
    assert _is_allow(result)


async def test_paused_run_still_allows_read_only(station_db):
    """Pause doesn't starve the UI — Read/Grep continue flowing."""
    run_control.set_run_paused("run-paused", True)
    result = await policy_decide(
        "Read", {"file_path": "/etc/hosts"}, None,
        AutonomyLevel.AUTO,
        run_id="run-paused", agent_id="lead",
    )
    assert _is_allow(result)


async def test_drain_pending_controls_marks_consumed(station_db):
    conn = sqlite3.connect(station_db)
    conn.execute(
        "INSERT INTO run_controls (run_id, action, payload, requested_by, requested_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        ("run-1", "pause", None, "api"),
    )
    conn.execute(
        "INSERT INTO run_controls (run_id, action, payload, requested_by, requested_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        ("run-1", "message", '{"text": "hello"}', "api"),
    )
    conn.commit()
    conn.close()

    rows = run_control.drain_pending_controls("run-1")
    assert [r.action for r in rows] == ["pause", "message"]
    assert rows[1].payload == {"text": "hello"}

    # Second drain returns nothing — rows are marked consumed.
    assert run_control.drain_pending_controls("run-1") == []
