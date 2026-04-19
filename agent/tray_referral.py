"""Tray referral helper — ADR-0001 (deferred Phase 2 wiring).

When ``STATION_TRAY_REFERRAL=1`` is set, destructive bash calls at manual /
assisted and edits at manual are referred to the operator instead of being
deny-returned. We POST to ``/api/permissions`` (which inserts a row and fires
the ``permission_request`` SSE) and then poll sqlite for the row's status.

Returns ``PermissionResultAllow`` on approve; ``PermissionResultDeny`` on
deny, timeout, or any error — deny is always the safe fallback.

Design notes:
- The backend does timeout sweeping on read; we also enforce a local wall
  clock so a disconnected backend can't hang the agent forever.
- sqlite polling is cheaper than HTTP polling and avoids the auth dance.
- Never raises — the SDK expects a PermissionResult.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from agent.audit_hook import _db_path
from agent.auto_mode import AutonomyLevel

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_URL = "http://127.0.0.1:8420"
DEFAULT_TIMEOUT_SECONDS = 300  # matches backend STATION_PERMISSION_TRAY_TIMEOUT_SECONDS default
POLL_INTERVAL_SECONDS = 1.0
REFERRAL_EVENT_TYPE = "auto_mode_referral"


def referral_enabled() -> bool:
    """Gate for the tray-referral path. Default OFF for safe rollout."""
    raw = os.environ.get("STATION_TRAY_REFERRAL", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _backend_url() -> str:
    return os.environ.get("STATION_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def _timeout_seconds() -> int:
    raw = os.environ.get("STATION_PERMISSION_TRAY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        return max(30, int(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _auth_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("STATION_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _post_request(payload: dict[str, Any]) -> bool:
    """POST /api/permissions — returns True on 2xx, False otherwise."""
    url = f"{_backend_url()}/api/permissions"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=_auth_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        # 201 Created raises as HTTPError in some stdlib paths; treat 2xx as OK.
        if 200 <= exc.code < 300:
            return True
        logger.warning("tray_referral: POST failed http=%s body=%s", exc.code, exc.read()[:200])
        return False
    except Exception as exc:
        logger.warning("tray_referral: POST failed: %s", exc)
        return False


def _read_status(request_id: str, db_path: str | None = None) -> tuple[str | None, str | None]:
    """Return (status, resolution_note) for the row, or (None, None) if missing."""
    path = db_path or _db_path()
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            cur = conn.execute(
                "SELECT status, resolution_note FROM permission_requests WHERE request_id = ?",
                (request_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("tray_referral: status read failed: %s", exc)
        return None, None
    if not row:
        return None, None
    return row[0], row[1]


def _write_referral_audit(
    *,
    run_id: str,
    agent_id: str,
    level: AutonomyLevel,
    tool_name: str,
    tool_input: dict[str, Any],
    request_id: str,
    final_status: str,
    reason: str | None,
    db_path: str | None = None,
) -> None:
    """One row per referral, event_type='auto_mode_referral'. Never raises."""
    path = db_path or _db_path()
    payload = {
        "level": level.value,
        "tool_name": tool_name,
        "tool_input": _summarise_input(tool_input),
        "request_id": request_id,
        "final_status": final_status,
        "reason": reason,
    }
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(
                """
                INSERT INTO agent_events
                    (workflow_id, run_id, agent_id, event_type, team_name, event_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    run_id,
                    run_id,
                    agent_id,
                    REFERRAL_EVENT_TYPE,
                    None,
                    json.dumps(payload, default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("tray_referral: audit write failed: %s", exc)


def _summarise_input(tool_input: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in tool_input.items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + f"…[+{len(value) - limit} chars]"
        else:
            out[key] = value
    return out


async def refer_to_operator(
    *,
    run_id: str,
    agent_id: str,
    level: AutonomyLevel,
    tool_name: str,
    tool_input: dict[str, Any],
    reason: str,
    db_path: str | None = None,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
) -> PermissionResultAllow | PermissionResultDeny:
    """Raise a tray request and block until resolved.

    Safe fallback: any failure to post or poll returns a deny so the agent
    never silently proceeds on an unreachable backend.
    """
    request_id = f"tray-{uuid.uuid4().hex[:16]}"

    posted = _post_request({
        "request_id": request_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "autonomy_level": level.value,
        "reason": reason,
    })
    if not posted:
        _write_referral_audit(
            run_id=run_id, agent_id=agent_id, level=level,
            tool_name=tool_name, tool_input=tool_input,
            request_id=request_id, final_status="post_failed", reason=reason,
            db_path=db_path,
        )
        return PermissionResultDeny(
            message=f"tray referral unreachable; defaulting to deny at {level.value}",
        )

    deadline = time.monotonic() + (timeout_seconds or _timeout_seconds())
    final_status: str = "timed_out"
    note: str | None = None
    while time.monotonic() < deadline:
        status, resolution_note = _read_status(request_id, db_path=db_path)
        if status and status != "pending":
            final_status = status
            note = resolution_note
            break
        # Yield to the loop so operator updates can flow in.
        await asyncio.sleep(poll_interval_seconds)

    _write_referral_audit(
        run_id=run_id, agent_id=agent_id, level=level,
        tool_name=tool_name, tool_input=tool_input,
        request_id=request_id, final_status=final_status, reason=reason,
        db_path=db_path,
    )

    if final_status == "approved":
        return PermissionResultAllow()
    if final_status == "denied":
        return PermissionResultDeny(
            message=f"operator denied tray request {request_id}"
            + (f": {note}" if note else ""),
        )
    # timed_out, unknown, etc.
    return PermissionResultDeny(
        message=f"tray request {request_id} final_status={final_status}",
    )
