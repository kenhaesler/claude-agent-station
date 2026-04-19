"""Unit tests for the tray-referral helper — ADR-0001 (deferred Phase 2).

Covers:
- When STATION_TRAY_REFERRAL is off, destructive calls deny-return as before.
- When on, a referral row is posted and polled; approve → allow, deny → deny,
  timeout → deny.
- ALWAYS_DENY patterns never reach the tray (no row posted).
- Post failure → deny fallback (agent never silently proceeds).
- Audit row written for every referral outcome.
- `auto` level bypasses the referral path entirely for destructive bash.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent.auto_mode import AutonomyLevel, policy_decide
from agent.tray_referral import refer_to_operator, referral_enabled
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def _init_db(path: Path) -> None:
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
    conn.execute(
        """
        CREATE TABLE permission_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_input TEXT NOT NULL,
            autonomy_level TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution_note TEXT,
            created_at TEXT,
            resolved_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _set_row_status(path: Path, request_id: str, status: str, note: str | None = None) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "UPDATE permission_requests SET status = ?, resolution_note = ? WHERE request_id = ?",
        (status, note, request_id),
    )
    conn.commit()
    conn.close()


def _fetch_audit(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM agent_events ORDER BY event_id")]
    conn.close()
    return rows


def _fetch_tray(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM permission_requests")]
    conn.close()
    return rows


# --- Flag gating -----------------------------------------------------------


def test_referral_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STATION_TRAY_REFERRAL", raising=False)
    assert referral_enabled() is False


@pytest.mark.parametrize("v", ["1", "true", "TRUE", "yes", "on"])
def test_referral_enabled_flag_accepts_common_truthy(monkeypatch, v):
    monkeypatch.setenv("STATION_TRAY_REFERRAL", v)
    assert referral_enabled() is True


@pytest.mark.parametrize("v", ["0", "false", "", "no", "off"])
def test_referral_disabled_flag_values(monkeypatch, v):
    monkeypatch.setenv("STATION_TRAY_REFERRAL", v)
    assert referral_enabled() is False


# --- Integration via policy_decide (flag OFF) ------------------------------


async def test_destructive_bash_denies_when_flag_off(monkeypatch):
    """Legacy behaviour: flag off → deny-return identical to pre-wiring."""
    monkeypatch.delenv("STATION_TRAY_REFERRAL", raising=False)
    result = await policy_decide(
        "Bash",
        {"command": "rm -rf node_modules"},
        None,
        AutonomyLevel.ASSISTED,
        run_id="run-001",
    )
    assert isinstance(result, PermissionResultDeny)
    assert "destructive bash at assisted" in result.message


async def test_edit_denies_at_manual_when_flag_off(monkeypatch):
    monkeypatch.delenv("STATION_TRAY_REFERRAL", raising=False)
    result = await policy_decide(
        "Edit", {"file_path": "/tmp/x"}, None, AutonomyLevel.MANUAL, run_id="run-001",
    )
    assert isinstance(result, PermissionResultDeny)


async def test_always_deny_still_wins_even_with_flag_on(monkeypatch, tmp_path):
    """ALWAYS_DENY never gets referred — push-to-main etc. stays deny-return."""
    monkeypatch.setenv("STATION_TRAY_REFERRAL", "1")
    db = tmp_path / "test.db"
    _init_db(db)
    monkeypatch.setenv("STATION_DB_PATH", str(db))

    result = await policy_decide(
        "Bash",
        {"command": "git push --force origin main"},
        None,
        AutonomyLevel.ASSISTED,
        run_id="run-001",
    )
    assert isinstance(result, PermissionResultDeny)
    assert "always-deny" in result.message
    assert _fetch_tray(db) == []  # no row posted


async def test_auto_level_bypasses_referral_for_destructive_bash(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_TRAY_REFERRAL", "1")
    db = tmp_path / "test.db"
    _init_db(db)
    monkeypatch.setenv("STATION_DB_PATH", str(db))

    result = await policy_decide(
        "Bash",
        {"command": "rm -rf stale-build"},
        None,
        AutonomyLevel.AUTO,
        run_id="run-001",
    )
    assert isinstance(result, PermissionResultAllow)
    assert _fetch_tray(db) == []


# --- refer_to_operator (direct) -------------------------------------------


def _stub_post_ok(monkeypatch, tmp_path: Path):
    """Patch _post_request to insert the row directly into sqlite, emulating
    what the backend's POST /api/permissions would do, without the HTTP hop."""
    db = tmp_path / "test.db"
    _init_db(db)
    monkeypatch.setenv("STATION_DB_PATH", str(db))

    def _fake_post(payload: dict) -> bool:
        import json as _json
        conn = sqlite3.connect(str(db))
        conn.execute(
            """INSERT INTO permission_requests
               (request_id, run_id, agent_id, tool_name, tool_input,
                autonomy_level, reason, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                payload["request_id"], payload["run_id"], payload["agent_id"],
                payload["tool_name"], _json.dumps(payload["tool_input"]),
                payload["autonomy_level"], payload.get("reason"),
            ),
        )
        conn.commit()
        conn.close()
        return True

    monkeypatch.setattr("agent.tray_referral._post_request", _fake_post)
    return db


async def test_approve_resolution_returns_allow(monkeypatch, tmp_path):
    db = _stub_post_ok(monkeypatch, tmp_path)

    # Resolve the request BEFORE calling refer_to_operator — the first poll
    # will see the approved row and return immediately.
    import uuid as _uuid
    orig_uuid4 = _uuid.uuid4
    captured: list[str] = []
    def _fake_uuid4():
        val = orig_uuid4()
        captured.append(val.hex[:16])
        return val
    monkeypatch.setattr("uuid.uuid4", _fake_uuid4)

    import asyncio
    async def _run():
        # Start referral; preapprove in a side task.
        task = asyncio.create_task(refer_to_operator(
            run_id="run-001", agent_id="lead", level=AutonomyLevel.ASSISTED,
            tool_name="Bash", tool_input={"command": "rm -rf build"},
            reason="destructive",
            poll_interval_seconds=0.05, timeout_seconds=5,
        ))
        # Wait a beat, then flip the row.
        await asyncio.sleep(0.1)
        request_id = f"tray-{captured[0]}"
        _set_row_status(db, request_id, "approved", note="operator sign-off")
        return await task

    result = await _run()
    assert isinstance(result, PermissionResultAllow)
    audit = _fetch_audit(db)
    assert len(audit) == 1
    assert audit[0]["event_type"] == "auto_mode_referral"
    assert '"final_status": "approved"' in audit[0]["event_data"]


async def test_deny_resolution_returns_deny(monkeypatch, tmp_path):
    db = _stub_post_ok(monkeypatch, tmp_path)

    captured: list[str] = []
    import uuid as _uuid
    orig = _uuid.uuid4
    def _fake():
        val = orig()
        captured.append(val.hex[:16])
        return val
    monkeypatch.setattr("uuid.uuid4", _fake)

    import asyncio
    async def _run():
        task = asyncio.create_task(refer_to_operator(
            run_id="run-002", agent_id="lead", level=AutonomyLevel.MANUAL,
            tool_name="Edit", tool_input={"file_path": "/tmp/x"},
            reason="edit at manual",
            poll_interval_seconds=0.05, timeout_seconds=5,
        ))
        await asyncio.sleep(0.1)
        _set_row_status(db, f"tray-{captured[0]}", "denied", note="nope")
        return await task

    result = await _run()
    assert isinstance(result, PermissionResultDeny)
    assert "operator denied" in result.message
    assert "nope" in result.message


async def test_timeout_resolution_returns_deny(monkeypatch, tmp_path):
    _stub_post_ok(monkeypatch, tmp_path)

    result = await refer_to_operator(
        run_id="run-003", agent_id="lead", level=AutonomyLevel.ASSISTED,
        tool_name="Bash", tool_input={"command": "rm -rf out"},
        reason="destructive",
        poll_interval_seconds=0.05, timeout_seconds=1,  # short
    )
    assert isinstance(result, PermissionResultDeny)
    assert "timed_out" in result.message


async def test_post_failure_denies_and_audits(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    _init_db(db)
    monkeypatch.setenv("STATION_DB_PATH", str(db))
    monkeypatch.setattr("agent.tray_referral._post_request", lambda payload: False)

    result = await refer_to_operator(
        run_id="run-004", agent_id="lead", level=AutonomyLevel.ASSISTED,
        tool_name="Bash", tool_input={"command": "rm -rf /tmp/x"},
        reason="destructive", poll_interval_seconds=0.05, timeout_seconds=1,
    )
    assert isinstance(result, PermissionResultDeny)
    assert "unreachable" in result.message
    audit = _fetch_audit(db)
    assert len(audit) == 1
    assert '"final_status": "post_failed"' in audit[0]["event_data"]


# --- policy_decide routing (flag ON) --------------------------------------


async def test_destructive_bash_routes_to_tray_when_flag_on(monkeypatch, tmp_path):
    db = _stub_post_ok(monkeypatch, tmp_path)
    monkeypatch.setenv("STATION_TRAY_REFERRAL", "1")

    captured: list[str] = []
    import uuid as _uuid
    orig = _uuid.uuid4
    def _fake():
        val = orig()
        captured.append(val.hex[:16])
        return val
    monkeypatch.setattr("uuid.uuid4", _fake)

    import asyncio
    async def _run():
        task = asyncio.create_task(policy_decide(
            "Bash", {"command": "git reset --hard HEAD~1"}, None,
            AutonomyLevel.ASSISTED,
            run_id="run-200", agent_id="teammate-issue-worker",
        ))
        await asyncio.sleep(0.1)
        _set_row_status(db, f"tray-{captured[0]}", "approved")
        return await task

    result = await _run()
    assert isinstance(result, PermissionResultAllow)
    tray = _fetch_tray(db)
    assert len(tray) == 1
    assert tray[0]["agent_id"] == "teammate-issue-worker"
    assert tray[0]["autonomy_level"] == "assisted"


async def test_no_run_id_skips_referral_even_when_flag_on(monkeypatch, tmp_path):
    """Without run_id we can't tag the row — fall back to deny so the agent
    never proceeds on an un-auditable call."""
    monkeypatch.setenv("STATION_TRAY_REFERRAL", "1")
    db = tmp_path / "test.db"
    _init_db(db)
    monkeypatch.setenv("STATION_DB_PATH", str(db))

    result = await policy_decide(
        "Bash", {"command": "rm -rf build"}, None, AutonomyLevel.ASSISTED,
    )
    assert isinstance(result, PermissionResultDeny)
    assert _fetch_tray(db) == []
