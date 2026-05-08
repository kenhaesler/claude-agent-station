"""Auto Mode audit hook — ADR-0001 + per-tool-call audit log (issue #73).

Two append-only audit trails:

* ``agent_events`` (event_type=``auto_mode_decision``) — every
  ``can_use_tool`` decision and the autonomy level that produced it.
* ``audit_log`` — per-tool-call telemetry (status, exit code, stdout/stderr
  tails, durations). Written in two phases via the SDK's
  ``PreToolUse`` / ``PostToolUse`` hooks; rows are matched by
  ``idempotency_key`` (= SDK ``tool_use_id``).

Design:
- ``make_audited_policy(run_id, level)`` composes the policy engine with a
  best-effort sqlite write. The returned coroutine matches the SDK's
  ``can_use_tool`` signature.
- ``make_pre_tool_hook`` / ``make_post_tool_hook`` return ``HookCallback``s
  for SDK ``hooks={"PreToolUse": [...], "PostToolUse": [...]}``.
- Audit writes never raise — a failure to log must not break tool execution.
  Errors are logged at WARNING.
- Uses raw ``sqlite3`` so the agent does not import the dashboard backend.
- Database path comes from ``STATION_DB_PATH`` (matches ``Settings.db_path``).
"""

from __future__ import annotations

import asyncio
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

# Truncation limit for stdout_tail / stderr_tail captured in audit_log rows.
TAIL_LIMIT = 4_000

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


# --- audit_log writers (issue #73) -----------------------------------------


def _action_kind(tool_name: str) -> str:
    """Map an SDK tool name to a stable, lowercase action_kind tag."""
    return f"tool.{tool_name.lower()}"


def _tail(text: str, limit: int = TAIL_LIMIT) -> str:
    """Return at most ``limit`` chars from the end of ``text``."""
    if len(text) <= limit:
        return text
    return "…[+{} chars]\n".format(len(text) - limit) + text[-limit:]


def _coerce_tail(value: Any, limit: int = TAIL_LIMIT) -> str | None:
    """Best-effort string coercion + tail truncation for arbitrary tool output."""
    if value is None:
        return None
    if isinstance(value, str):
        return _tail(value, limit)
    try:
        return _tail(json.dumps(value, default=str), limit)
    except Exception:
        return _tail(str(value), limit)


def _extract_outcome(tool_response: Any) -> tuple[str, int | None, str | None, str | None]:
    """Pull (status, exit_code, stdout_tail, stderr_tail) from an SDK tool response.

    Different tools return different shapes; this is a heuristic that handles
    Bash (``output``/``stdout``/``stderr``/``exit_code``/``is_error``) and
    falls back to dumping the response into ``stdout_tail`` for everything else.
    Always returns ``status="ok"`` unless the response signals an error.
    """
    if isinstance(tool_response, dict):
        is_error = bool(tool_response.get("is_error") or tool_response.get("error"))
        exit_code = tool_response.get("exit_code")
        if not isinstance(exit_code, int):
            exit_code = None
        # Use sentinel default so that an explicit ``stdout=""`` is preserved
        # rather than falling through to ``output``.
        _missing = object()
        stdout = tool_response.get("stdout", _missing)
        if stdout is _missing:
            stdout = tool_response.get("output")
        stderr = tool_response.get("stderr")
        # If the response is a single-field dict with a non-stdout key, capture it.
        if stdout is None and stderr is None and "is_error" not in tool_response:
            stdout = tool_response
        status = "error" if is_error or (exit_code is not None and exit_code != 0) else "ok"
        return status, exit_code, _coerce_tail(stdout), _coerce_tail(stderr)
    # Non-dict response: stash everything into stdout_tail.
    return "ok", None, _coerce_tail(tool_response), None


def write_audit_start(
    *,
    idempotency_key: str,
    run_id: str,
    actor: str,
    tool_name: str,
    tool_input: dict[str, Any],
    trace_id: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert a ``status='started'`` row keyed by ``idempotency_key``. Never raises.

    Uses ``INSERT OR IGNORE`` so a retry that re-fires the same ``tool_use_id``
    does not duplicate the row — the unique constraint provides crash-survivable
    idempotency.
    """
    path = db_path or _db_path()
    detail = json.dumps(
        {"tool_name": tool_name, "tool_input": _summarise_input(tool_input)},
        default=str,
    )
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_log
                    (idempotency_key, trace_id, run_id, actor, action_kind,
                     action_detail, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, 'started', ?)
                """,
                (
                    idempotency_key,
                    trace_id,
                    run_id,
                    actor,
                    _action_kind(tool_name),
                    detail,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("audit_hook: failed to write audit_log start: %s", exc)
    except Exception as exc:  # pragma: no cover — belt and braces
        logger.warning("audit_hook: unexpected error writing audit_log start: %s", exc)


def write_audit_finish(
    *,
    idempotency_key: str,
    tool_response: Any,
    db_path: str | None = None,
) -> None:
    """Update the row keyed by ``idempotency_key`` with terminal status. Never raises.

    If the corresponding ``write_audit_start`` row is missing (start hook
    skipped or DB cleared), this is a silent no-op — auditing is best-effort.
    """
    path = db_path or _db_path()
    status, exit_code, stdout_tail, stderr_tail = _extract_outcome(tool_response)
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(
                """
                UPDATE audit_log
                   SET status = ?,
                       exit_code = ?,
                       stdout_tail = ?,
                       stderr_tail = ?,
                       finished_at = ?
                 WHERE idempotency_key = ?
                """,
                (
                    status,
                    exit_code,
                    stdout_tail,
                    stderr_tail,
                    datetime.now(timezone.utc).isoformat(),
                    idempotency_key,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("audit_hook: failed to write audit_log finish: %s", exc)
    except Exception as exc:  # pragma: no cover — belt and braces
        logger.warning("audit_hook: unexpected error writing audit_log finish: %s", exc)


# --- SDK PreToolUse / PostToolUse hook factories ---------------------------


def make_pre_tool_hook(
    *,
    run_id: str,
    actor: str = "lead",
    trace_id: str | None = None,
    db_path: str | None = None,
):
    """Return an SDK ``PreToolUse`` hook callback that writes a 'started' row."""

    async def pre_hook(input_data, _tool_use_id_arg, _ctx):
        try:
            tool_name = input_data.get("tool_name") if isinstance(input_data, dict) else getattr(input_data, "tool_name", "")
            tool_input = input_data.get("tool_input") if isinstance(input_data, dict) else getattr(input_data, "tool_input", {})
            tool_use_id = input_data.get("tool_use_id") if isinstance(input_data, dict) else getattr(input_data, "tool_use_id", None)
            if tool_use_id:
                # Off-load the blocking sqlite3 write so the orchestrator's
                # event loop is not held up by WAL contention.
                await asyncio.to_thread(
                    write_audit_start,
                    idempotency_key=str(tool_use_id),
                    run_id=run_id,
                    actor=actor,
                    tool_name=str(tool_name or ""),
                    tool_input=tool_input or {},
                    trace_id=trace_id,
                    db_path=db_path,
                )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("audit_hook: pre_hook error: %s", exc)
        return {}

    return pre_hook


def make_post_tool_hook(
    *,
    run_id: str,  # noqa: ARG001 — kept for symmetry / future use
    actor: str = "lead",  # noqa: ARG001
    db_path: str | None = None,
):
    """Return an SDK ``PostToolUse`` hook callback that updates the row."""

    async def post_hook(input_data, _tool_use_id_arg, _ctx):
        try:
            tool_use_id = input_data.get("tool_use_id") if isinstance(input_data, dict) else getattr(input_data, "tool_use_id", None)
            tool_response = input_data.get("tool_response") if isinstance(input_data, dict) else getattr(input_data, "tool_response", None)
            if tool_use_id:
                # Off-load the blocking sqlite3 write so the orchestrator's
                # event loop is not held up by WAL contention.
                await asyncio.to_thread(
                    write_audit_finish,
                    idempotency_key=str(tool_use_id),
                    tool_response=tool_response,
                    db_path=db_path,
                )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("audit_hook: post_hook error: %s", exc)
        return {}

    return post_hook
