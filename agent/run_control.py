"""Mission Control — operator intervention for running agents (Phase A).

The backend writes intervention rows to ``run_controls`` and the
``station_control`` singleton; the orchestrator drains the queue between
SDK messages and applies the actions. The policy engine additionally reads
``station_control.global_pause`` and the in-memory per-run pause set so
every tool call can be routed to the permission tray on demand — even in
``auto`` mode.

All sqlite reads are best-effort. If the DB is unreachable, agents fall
back to their configured autonomy level (never stricter than the operator
asked for, since a lock-out while the dashboard is down would be worse than
letting the agent continue under the level it was started with).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent.audit_hook import _db_path

logger = logging.getLogger(__name__)


class OrchestratorStopRequested(Exception):
    """Raised when the operator requests a cooperative stop of a run."""


# In-memory flag set — a run_id here forces every subsequent tool call to
# defer to the permission tray regardless of the run's autonomy level. The
# orchestrator adds/removes entries based on ``run_controls`` rows.
_paused_runs: set[str] = set()


def set_run_paused(run_id: str, paused: bool) -> None:
    if paused:
        _paused_runs.add(run_id)
    else:
        _paused_runs.discard(run_id)


def is_run_paused(run_id: str | None) -> bool:
    if not run_id:
        return False
    return run_id in _paused_runs


def is_global_paused(db_path: str | None = None) -> bool:
    """Best-effort read of ``station_control.global_pause``. Returns False on
    any error so a broken DB doesn't surprise the agent with a lock-up."""
    path = db_path or _db_path()
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        try:
            cur = conn.execute(
                "SELECT global_pause FROM station_control WHERE id = 1"
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


@dataclass
class RunControlRow:
    id: int
    run_id: str
    action: str  # 'pause' | 'resume' | 'stop' | 'message'
    payload: dict[str, Any] | None
    requested_by: str | None


def drain_pending_controls(
    run_id: str,
    db_path: str | None = None,
) -> list[RunControlRow]:
    """Fetch unconsumed run_controls for ``run_id`` ordered by id, mark them
    consumed, and return them. Called by the orchestrator between SDK
    messages. Returns an empty list on any DB error (best-effort)."""
    path = db_path or _db_path()
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        try:
            cur = conn.execute(
                "SELECT id, run_id, action, payload, requested_by "
                "FROM run_controls "
                "WHERE run_id = ? AND consumed_at IS NULL "
                "ORDER BY id ASC",
                (run_id,),
            )
            rows = cur.fetchall()
            if not rows:
                return []
            ids = [r[0] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE run_controls SET consumed_at = datetime('now') "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("run_control: failed to drain controls for %s: %s", run_id, exc)
        return []

    out: list[RunControlRow] = []
    for row_id, r_run_id, action, payload_raw, requested_by in rows:
        payload: dict[str, Any] | None = None
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = None
        out.append(RunControlRow(
            id=row_id,
            run_id=r_run_id,
            action=action,
            payload=payload,
            requested_by=requested_by,
        ))
    return out
