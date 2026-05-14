"""Run timeline service — merges five source tables into one ordered stream.

Owns the heap-merge, the per-source projection, and cursor-based pagination.
The HTTP layer (``routers/runs.py``) only orchestrates the FastAPI shape.

See spec: docs/superpowers/specs/2026-05-14-issue-387-run-timeline-api.md
"""
from __future__ import annotations

import asyncio
import base64
import heapq
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


_VERDICT_EVENT_TYPES = (
    "verdict_execute",
    "manager_review",
    "manager_review_complete",
)


async def _teammate_events(
    db: AsyncSession,
    run_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunTimelineEvent]:
    rows = (
        await db.execute(
            select(CoordinatorTask)
            .where(CoordinatorTask.run_id == run_id)
            .order_by(CoordinatorTask.started_at)
        )
    ).scalars().all()
    out: list[RunTimelineEvent] = []
    for row in rows:
        spawn_t = row.claimed_at or row.started_at
        if spawn_t is not None and _within(spawn_t, since, until):
            out.append(
                RunTimelineEvent(
                    t=spawn_t,
                    kind="teammate",
                    event="teammate.spawned",
                    source="coordinator_tasks",
                    source_id=row.id,
                    agent=row.teammate_agent_id,
                    data={"task_id": row.id, "title": row.title, "status": row.status},
                )
            )
        terminal_t = (
            row.finished_at if row.status in ("completed", "failed", "orphaned") else None
        )
        if terminal_t is not None and _within(terminal_t, since, until):
            out.append(
                RunTimelineEvent(
                    t=terminal_t,
                    kind="teammate",
                    event="teammate.completed",
                    source="coordinator_tasks",
                    source_id=row.id,
                    agent=row.teammate_agent_id,
                    data={"status": row.status, "result_summary": row.result_summary},
                )
            )
    out.sort(key=lambda e: (e.t, e.source, e.source_id))
    return out


async def _verdict_events(
    db: AsyncSession,
    run_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunTimelineEvent]:
    rows = (
        await db.execute(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id)
            .where(AgentEvent.event_type.in_(_VERDICT_EVENT_TYPES))
            .order_by(AgentEvent.created_at)
        )
    ).scalars().all()
    out: list[RunTimelineEvent] = []
    for row in rows:
        if not _within(row.created_at, since, until):
            continue
        out.append(
            RunTimelineEvent(
                t=row.created_at,
                kind="verdict",
                event=row.event_type,
                source="agent_events",
                source_id=str(row.event_id),
                agent=row.agent_id,
                data=_decode_json(row.event_data),
            )
        )
    return out


async def _conflict_events(
    db: AsyncSession,
    run_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunTimelineEvent]:
    # ConflictResolution rows aren't keyed by run_id directly — they're keyed
    # by branch. Bridge via the Run's branch column.
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None or run.branch is None:
        return []
    rows = (
        await db.execute(
            select(ConflictResolution)
            .where(ConflictResolution.branch == run.branch)
            .order_by(ConflictResolution.started_at)
        )
    ).scalars().all()
    out: list[RunTimelineEvent] = []
    for row in rows:
        if _within(row.started_at, since, until):
            out.append(
                RunTimelineEvent(
                    t=row.started_at,
                    kind="conflict",
                    event="conflict.started",
                    source="conflict_resolutions",
                    source_id=str(row.id),
                    agent=None,
                    data={"branch": row.branch, "phase": row.phase_reached},
                )
            )
        if row.finished_at is not None and _within(row.finished_at, since, until):
            out.append(
                RunTimelineEvent(
                    t=row.finished_at,
                    kind="conflict",
                    event=f"conflict.{row.outcome}",
                    source="conflict_resolutions",
                    source_id=str(row.id),
                    agent=None,
                    data={"outcome": row.outcome, "phase": row.phase_reached},
                )
            )
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


from app.schemas import RunTimelinePage

ALL_KINDS = frozenset({"lifecycle", "tool", "teammate", "verdict", "conflict"})
_SOURCE_FOR_KIND = {
    "lifecycle": _lifecycle_events,
    "tool": _audit_events,
    "teammate": _teammate_events,
    "verdict": _verdict_events,
    "conflict": _conflict_events,
}


async def build_timeline(
    db: AsyncSession,
    run_id: str,
    *,
    kinds: set[str] | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    cursor: TimelineCursor | None,
) -> RunTimelinePage:
    """Merge per-source streams, apply cursor / limit, return one page."""
    selected = ALL_KINDS if kinds is None else (kinds & ALL_KINDS)
    if not selected:
        return RunTimelinePage(run_id=run_id, events=[], next_cursor=None, has_more=False)

    coros = [_SOURCE_FOR_KIND[k](db, run_id, since=since, until=until) for k in selected]
    streams = await asyncio.gather(*coros)
    merged_iter = heapq.merge(
        *streams,
        key=lambda e: (e.t, e.source, e.source_id),
    )

    cursor_key = (
        (cursor.t, cursor.source, cursor.source_id) if cursor is not None else None
    )
    take = limit + 1  # read one extra to compute has_more
    out: list[RunTimelineEvent] = []
    for ev in merged_iter:
        if cursor_key is not None and (ev.t, ev.source, ev.source_id) <= cursor_key:
            continue
        out.append(ev)
        if len(out) >= take:
            break

    has_more = len(out) > limit
    if has_more:
        out = out[:limit]
    next_cursor = (
        TimelineCursor(t=out[-1].t, source=out[-1].source, source_id=out[-1].source_id).encode()
        if has_more and out
        else None
    )
    return RunTimelinePage(
        run_id=run_id, events=out, next_cursor=next_cursor, has_more=has_more
    )
