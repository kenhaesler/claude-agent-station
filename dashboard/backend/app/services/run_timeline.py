"""Run timeline service — merges five source tables into one ordered stream.

Owns the heap-merge, the per-source projection, and cursor-based pagination.
The HTTP layer (``routers/runs.py``) only orchestrates the FastAPI shape.

See spec: docs/superpowers/specs/2026-05-14-issue-387-run-timeline-api.md
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TimelineCursor:
    t: datetime
    source: str
    source_id: str

    def encode(self) -> str:
        payload = {
            "t": self.t.isoformat(),
            "s": self.source,
            "i": self.source_id,
        }
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    @classmethod
    def decode(cls, encoded: str) -> "TimelineCursor":
        try:
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
            return cls(
                t=datetime.fromisoformat(data["t"]),
                source=data["s"],
                source_id=str(data["i"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid cursor: {exc}") from exc


from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentEvent,
    AuditEntry,
    ConflictResolution,
    CoordinatorTask,
    Run,
)
from app.schemas import RunTimelineEvent


def _decode_json(value: Any) -> dict | None:
    """Dialect-agnostic JSON decoder.

    SQLite returns the column as ``str``; Postgres JSONB returns dicts
    directly. Returns None if value is None or unparseable.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _within(t: datetime, since: datetime | None, until: datetime | None) -> bool:
    if since is not None and t < since:
        return False
    if until is not None and t >= until:
        return False
    return True


async def _lifecycle_events(
    db: AsyncSession,
    run_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunTimelineEvent]:
    out: list[RunTimelineEvent] = []
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is not None:
        if run.started_at is not None and _within(run.started_at, since, until):
            out.append(
                RunTimelineEvent(
                    t=run.started_at,
                    kind="lifecycle",
                    event="run_start",
                    source="runs",
                    source_id=run.run_id,
                    agent=None,
                    data={"status": run.status, "project_id": run.project_id},
                )
            )
        if run.finished_at is not None and _within(run.finished_at, since, until):
            out.append(
                RunTimelineEvent(
                    t=run.finished_at,
                    kind="lifecycle",
                    event="run_complete",
                    source="runs",
                    source_id=run.run_id,
                    agent=None,
                    data={"verdict": run.verdict, "status": run.status},
                )
            )

    rows = (
        await db.execute(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id)
            .where(AgentEvent.event_type.like("lifecycle.%"))
            .order_by(AgentEvent.created_at)
        )
    ).scalars().all()
    for row in rows:
        if not _within(row.created_at, since, until):
            continue
        out.append(
            RunTimelineEvent(
                t=row.created_at,
                kind="lifecycle",
                event=row.event_type,
                source="agent_events",
                source_id=str(row.event_id),
                agent=row.agent_id,
                data=_decode_json(row.event_data),
            )
        )
    out.sort(key=lambda e: (e.t, e.source, e.source_id))
    return out


_AUDIT_TAIL_TRIM = 1024  # bytes; full payload still reachable via /api/audit


def _trim(text: str | None) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _AUDIT_TAIL_TRIM:
        return text, False
    return encoded[:_AUDIT_TAIL_TRIM].decode("utf-8", errors="replace"), True


async def _audit_events(
    db: AsyncSession,
    run_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunTimelineEvent]:
    rows = (
        await db.execute(
            select(AuditEntry)
            .where(AuditEntry.run_id == run_id)
            .order_by(AuditEntry.started_at)
        )
    ).scalars().all()
    out: list[RunTimelineEvent] = []
    for row in rows:
        if not _within(row.started_at, since, until):
            continue
        stdout_tail, stdout_trim = _trim(row.stdout_tail)
        stderr_tail, stderr_trim = _trim(row.stderr_tail)
        out.append(
            RunTimelineEvent(
                t=row.started_at,
                kind="tool",
                event=f"{row.action_kind}.{row.status}",
                source="audit_log",
                source_id=str(row.id),
                agent=row.actor,
                data={
                    "action_detail": _decode_json(row.action_detail),
                    "exit_code": row.exit_code,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "duration_ms": (
                        int((row.finished_at - row.started_at).total_seconds() * 1000)
                        if row.finished_at is not None
                        else None
                    ),
                    "truncated": stdout_trim or stderr_trim,
                },
            )
        )
    out.sort(key=lambda e: (e.t, e.source, e.source_id))
    return out
