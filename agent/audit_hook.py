"""Auto Mode audit hook — ADR-0001.

Records every `can_use_tool` decision made by :func:`agent.auto_mode.policy_decide`
to the ``agent_events`` table (see ``dashboard/backend/app/models.py``), so the
operator has a per-call audit trail of what was allowed/denied at which
autonomy level.

Design:
- ``make_audited_policy(run_id, level)`` composes the policy engine with a
  best-effort sqlite write. The returned coroutine matches the SDK's
  ``can_use_tool`` signature.
- Audit writes never raise — a failure to log must not break tool execution.
  Errors are logged at WARNING.
- Uses raw ``sqlite3`` so the agent does not import the dashboard backend.
- Database path comes from ``STATION_DB_PATH`` (matches ``Settings.db_path``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from agent.auto_mode import AutonomyLevel, policy_decide

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/var/lib/claude-agent-station/station.db"
EVENT_TYPE = "auto_mode_decision"

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def _db_path() -> str:
    return os.environ.get("STATION_DB_PATH", DEFAULT_DB_PATH)


def _summarise_input(tool_input: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    """Truncate long string values so audit rows don't store entire file bodies."""
    out: dict[str, Any] = {}
    for key, value in tool_input.items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + f"…[+{len(value) - limit} chars]"
        else:
            out[key] = value
    return out


def write_decision_event(
    *,
    run_id: str,
    agent_id: str,
    level: AutonomyLevel,
    tool_name: str,
    tool_input: dict[str, Any],
    decision: PermissionResultAllow | PermissionResultDeny,
    db_path: str | None = None,
) -> None:
    """Append one audit row. Never raises."""
    path = db_path or _db_path()
    allowed = isinstance(decision, PermissionResultAllow)
    reason = "" if allowed else getattr(decision, "message", "") or ""

    payload = {
        "level": level.value,
        "tool_name": tool_name,
        "tool_input": _summarise_input(tool_input),
        "decision": "allow" if allowed else "deny",
        "reason": reason,
    }

    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(
                """
                INSERT INTO agent_events
                    (workflow_id, run_id, agent_id, event_type, team_name, event_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_id,
                    agent_id,
                    EVENT_TYPE,
                    None,
                    json.dumps(payload, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("audit_hook: failed to write %s row: %s", EVENT_TYPE, exc)
    except Exception as exc:  # pragma: no cover — belt and braces
        logger.warning("audit_hook: unexpected error writing %s row: %s", EVENT_TYPE, exc)


def make_audited_policy(
    run_id: str,
    level: AutonomyLevel,
    *,
    agent_id: str = "lead",
    db_path: str | None = None,
) -> CanUseTool:
    """Return a ``can_use_tool`` callable that policy-decides then audits.

    Parameters
    ----------
    run_id
        The station run id (e.g. ``run-20260419T...``). Used as both
        ``workflow_id`` and ``run_id`` on the audit row.
    level
        The autonomy level for this run.
    agent_id
        Identifier for the caller — defaults to ``"lead"``. Teammates should
        pass e.g. ``"teammate-issue-worker"``.
    db_path
        Override the DB path. Tests use this; production uses ``STATION_DB_PATH``.
    """

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext | None,
    ) -> PermissionResultAllow | PermissionResultDeny:
        decision = await policy_decide(
            tool_name, tool_input, ctx, level,
            run_id=run_id, agent_id=agent_id,
        )
        write_decision_event(
            run_id=run_id,
            agent_id=agent_id,
            level=level,
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
            db_path=db_path,
        )
        return decision

    return can_use_tool
