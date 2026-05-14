# Run Timeline API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/runs/{run_id}/timeline` that JOINs across `runs`, `agent_events`, `audit_log`, `coordinator_tasks`, and `conflict_resolutions`, plus a "Timeline" tab on the run detail page — eliminating the "grep six places" workflow.

**Architecture:** A new service module (`dashboard/backend/app/services/run_timeline.py`) owns the merge logic; the existing `routers/runs.py` adds a thin endpoint that delegates to it. Each of five source queries (lifecycle, tool, teammate, verdict, conflict) is bounded by the indexed `run_id` and ordered by time; results are heap-merged in Python with `(t, source, source_id)` as the stable sort key. Pagination uses an opaque base64-encoded cursor. The frontend renders a fifth tab on `RunDetail.svelte` that consumes the endpoint, with filter chips and a "Load more" button.

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy 2 async / SQLite (Postgres-ready via `JSON` column accessors), Svelte 5 / TypeScript, pytest + httpx for tests.

**Tracking issue:** [#387](https://github.com/kenhaesler/claude-agent-station/issues/387)

**Spec:** `docs/superpowers/specs/2026-05-14-issue-387-run-timeline-api.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `dashboard/backend/app/services/run_timeline.py` | **new** | `build_timeline()`, source projection helpers, `TimelineCursor`, `TimelinePage` dataclasses. |
| `dashboard/backend/app/schemas.py` | modify | Add `RunTimelineEvent`, `RunTimelinePage` Pydantic models. |
| `dashboard/backend/app/routers/runs.py` | modify | Add `GET /{run_id}/timeline` endpoint near `get_run_full_context` (line 437). |
| `dashboard/backend/tests/test_run_timeline.py` | **new** | Unit tests: per-source projection, merge order, filter, pagination, cursor stability, `_decode_json` dialect helper. |
| `dashboard/backend/tests/integration/test_run_timeline.py` | **new** | End-to-end integration test against a synthetic run. |
| `dashboard/frontend/src/components/runs/TimelineTab.svelte` | **new** | The fifth tab: fetch, render, filter chips, "Load more". |
| `dashboard/frontend/src/pages/RunDetail.svelte` | modify | Add `'timeline'` to `TabId`, push tab entry, wire conditional block. |
| `dashboard/frontend/e2e/timeline.spec.ts` | **new** | Playwright: filter chip toggle, cursor pagination. |

---

## Setup (run once per execution session)

### Task 0: Sync local dev branch

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

Expected: `Already up to date.` or a fast-forward summary.

- [ ] **Step 2: Confirm backend tests pass on a clean tree**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runs.py tests/test_audit_log.py -q
```

Expected: all green.

- [ ] **Step 3: Create feature branch**

```bash
git checkout dev && git checkout -b feature/387-run-timeline-api
```

---

## Task 1: Schemas — RunTimelineEvent + RunTimelinePage

**Files:**
- Modify: `dashboard/backend/app/schemas.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (new)

- [ ] **Step 1: Write the failing schema-shape test**

Create `dashboard/backend/tests/test_run_timeline.py`:

```python
"""Run timeline API tests (issue #387)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas import RunTimelineEvent, RunTimelinePage


def test_timeline_event_shape():
    ev = RunTimelineEvent(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        kind="lifecycle",
        event="run_start",
        source="runs",
        source_id="run-20260513T151408Z",
        agent=None,
        data={"status": "started"},
    )
    assert ev.kind == "lifecycle"
    assert ev.data == {"status": "started"}


def test_timeline_page_default_empty():
    page = RunTimelinePage(run_id="run-x", events=[], next_cursor=None, has_more=False)
    assert page.events == []
    assert page.has_more is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_timeline_event_shape -q
```

Expected failure: `ImportError: cannot import name 'RunTimelineEvent' from 'app.schemas'`.

- [ ] **Step 3: Add the schemas**

Append to `dashboard/backend/app/schemas.py`:

```python
# --- Run timeline (issue #387) -----------------------------------------------

class RunTimelineEvent(BaseModel):
    """One row in the merged timeline.

    ``kind`` is the discriminator; ``data`` carries the source-specific
    payload verbatim. ``source`` + ``source_id`` are present for deep-link
    navigation to the per-source detail view.
    """

    t: datetime
    kind: str  # "lifecycle" | "tool" | "teammate" | "verdict" | "conflict"
    event: str
    source: str  # "runs" | "agent_events" | "audit_log" | "coordinator_tasks" | "conflict_resolutions"
    source_id: str
    agent: str | None = None
    data: dict | None = None


class RunTimelinePage(BaseModel):
    run_id: str
    events: list[RunTimelineEvent]
    next_cursor: str | None = None
    has_more: bool = False
```

Make sure `BaseModel` and `datetime` are already imported in this file.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/schemas.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): add RunTimelineEvent/RunTimelinePage schemas (#387)"
```

---

## Task 2: Cursor codec — `TimelineCursor.encode` / `decode`

**Files:**
- Modify: `dashboard/backend/app/services/run_timeline.py` (new)
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing test**

Append to `dashboard/backend/tests/test_run_timeline.py`:

```python
from datetime import datetime, timezone

from app.services.run_timeline import TimelineCursor


def test_cursor_roundtrip():
    c = TimelineCursor(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        source="audit_log",
        source_id="12345",
    )
    encoded = c.encode()
    assert isinstance(encoded, str)
    decoded = TimelineCursor.decode(encoded)
    assert decoded.t == c.t
    assert decoded.source == c.source
    assert decoded.source_id == c.source_id


def test_cursor_decode_rejects_garbage():
    with pytest.raises(ValueError):
        TimelineCursor.decode("not-base64!!!")
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_cursor_roundtrip -q
```

Expected: `ModuleNotFoundError: No module named 'app.services.run_timeline'`.

- [ ] **Step 3: Implement `TimelineCursor`**

Create `dashboard/backend/app/services/run_timeline.py`:

```python
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
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_timeline.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): add TimelineCursor base64 codec (#387)"
```

---

## Task 3: Source projection — lifecycle events

**Files:**
- Modify: `dashboard/backend/app/services/run_timeline.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing test**

Append to `dashboard/backend/tests/test_run_timeline.py`:

```python
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import AgentEvent, Run
from app.services.run_timeline import _lifecycle_events


@pytest.mark.asyncio
async def test_lifecycle_events_emits_run_start_and_complete():
    run_id = "run-tl-lifecycle-1"
    async with async_session() as db:
        db.add(
            Run(
                run_id=run_id,
                status="success",
                started_at=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
                verdict="APPROVE",
            )
        )
        db.add(
            AgentEvent(
                workflow_id="wf-1",
                run_id=run_id,
                agent_id="lead",
                event_type="lifecycle.orchestrator_complete",
                event_data="{}",
                created_at=datetime(2026, 5, 13, 15, 29, 50, tzinfo=timezone.utc),
            )
        )
        await db.commit()

    async with async_session() as db:
        events = await _lifecycle_events(db, run_id, since=None, until=None)

    kinds = [(e.event, e.source) for e in events]
    assert ("run_start", "runs") in kinds
    assert ("run_complete", "runs") in kinds
    assert ("lifecycle.orchestrator_complete", "agent_events") in kinds
    # Ordered ascending by t.
    assert [e.t for e in events] == sorted(e.t for e in events)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_lifecycle_events_emits_run_start_and_complete -q
```

Expected: `ImportError: cannot import name '_lifecycle_events'`.

- [ ] **Step 3: Implement `_lifecycle_events` and supporting helpers**

Append to `dashboard/backend/app/services/run_timeline.py`:

```python
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
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_timeline.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): emit lifecycle events from runs + agent_events (#387)"
```

---

## Task 4: Source projection — audit (tool) events

**Files:**
- Modify: `dashboard/backend/app/services/run_timeline.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing test**

```python
from app.services.run_timeline import _audit_events


@pytest.mark.asyncio
async def test_audit_events_emits_ok_and_error():
    run_id = "run-tl-audit-1"
    async with async_session() as db:
        db.add(
            AuditEntry(
                idempotency_key="k1",
                run_id=run_id,
                actor="teammate-backend",
                action_kind="tool.bash",
                action_detail='{"command":"ls"}',
                status="ok",
                exit_code=0,
                stdout_tail="ok",
                started_at=datetime(2026, 5, 13, 15, 15, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 15, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            AuditEntry(
                idempotency_key="k2",
                run_id=run_id,
                actor="teammate-qa",
                action_kind="tool.edit",
                action_detail=None,
                status="error",
                exit_code=1,
                stdout_tail=None,
                stderr_tail="boom",
                started_at=datetime(2026, 5, 13, 15, 16, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 16, 2, tzinfo=timezone.utc),
            )
        )
        await db.commit()

    async with async_session() as db:
        events = await _audit_events(db, run_id, since=None, until=None)

    assert [e.event for e in events] == ["tool.bash.ok", "tool.edit.error"]
    assert events[0].agent == "teammate-backend"
    assert events[0].data["exit_code"] == 0
    assert events[1].data["truncated"] is False
```

(The `AuditEntry` import at the top of the test module is already present from Task 3.)

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_audit_events_emits_ok_and_error -q
```

Expected: `ImportError: cannot import name '_audit_events'`.

- [ ] **Step 3: Implement `_audit_events`**

Append to `dashboard/backend/app/services/run_timeline.py`:

```python
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
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_timeline.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): emit tool events from audit_log (#387)"
```

---

## Task 5: Source projections — teammate, verdict, conflict

**Files:**
- Modify: `dashboard/backend/app/services/run_timeline.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing test**

```python
from app.models import ConflictResolution, CoordinatorTask
from app.services.run_timeline import (
    _conflict_events,
    _teammate_events,
    _verdict_events,
)


@pytest.mark.asyncio
async def test_teammate_verdict_conflict_events():
    run_id = "run-tl-mixed-1"
    async with async_session() as db:
        db.add(
            CoordinatorTask(
                id="t-1",
                run_id=run_id,
                project_repo="x/y",
                status="completed",
                started_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                claimed_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 5, 13, 15, 22, 0, tzinfo=timezone.utc),
                teammate_agent_id="backend",
                title="login api",
            )
        )
        db.add(
            AgentEvent(
                workflow_id="wf-2",
                run_id=run_id,
                agent_id="manager",
                event_type="verdict_execute",
                event_data='{"verdict":"PR"}',
                created_at=datetime(2026, 5, 13, 15, 25, 0, tzinfo=timezone.utc),
            )
        )
        db.add(
            ConflictResolution(
                branch="feature/x",
                repo="x/y",
                phase_reached="llm",
                outcome="resolved",
                started_at=datetime(2026, 5, 13, 15, 23, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 23, 30, tzinfo=timezone.utc),
            )
        )
        # ConflictResolution doesn't have run_id directly; tag via branch / runs.
        # For this test we simulate by giving the run a matching branch.
        db.add(
            Run(
                run_id=run_id,
                status="running",
                started_at=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
                branch="feature/x",
            )
        )
        await db.commit()

    async with async_session() as db:
        t_events = await _teammate_events(db, run_id, since=None, until=None)
        v_events = await _verdict_events(db, run_id, since=None, until=None)
        c_events = await _conflict_events(db, run_id, since=None, until=None)

    assert {e.event for e in t_events} == {"teammate.spawned", "teammate.completed"}
    assert t_events[0].agent == "backend"
    assert [e.event for e in v_events] == ["verdict_execute"]
    assert {e.event for e in c_events} == {"conflict.started", "conflict.resolved"}
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_teammate_verdict_conflict_events -q
```

Expected: `ImportError`.

- [ ] **Step 3: Implement the three projections**

Append to `dashboard/backend/app/services/run_timeline.py`:

```python
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
            row.updated_at if row.status in ("completed", "failed", "orphaned") else None
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
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_timeline.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): emit teammate/verdict/conflict events (#387)"
```

---

## Task 6: Merge + paginate — `build_timeline`

**Files:**
- Modify: `dashboard/backend/app/services/run_timeline.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from app.services.run_timeline import build_timeline


@pytest.mark.asyncio
async def test_build_timeline_merges_all_sources_in_order():
    run_id = "run-tl-merge-1"
    async with async_session() as db:
        db.add(
            Run(
                run_id=run_id,
                status="success",
                started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
                branch="feature/m",
                verdict="APPROVE",
            )
        )
        db.add(
            AuditEntry(
                idempotency_key="m-k1",
                run_id=run_id,
                actor="lead",
                action_kind="tool.bash",
                status="ok",
                started_at=datetime(2026, 5, 13, 15, 10, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 10, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            CoordinatorTask(
                id="m-t1",
                run_id=run_id,
                project_repo="x/y",
                status="completed",
                started_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
                claimed_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                teammate_agent_id="qa",
            )
        )
        await db.commit()

    async with async_session() as db:
        page = await build_timeline(db, run_id, kinds=None, since=None, until=None, limit=500, cursor=None)

    times = [e.t for e in page.events]
    assert times == sorted(times)
    assert page.has_more is False
    assert page.next_cursor is None
    kinds = {e.kind for e in page.events}
    assert {"lifecycle", "tool", "teammate"} <= kinds


@pytest.mark.asyncio
async def test_build_timeline_filters_by_kind():
    async with async_session() as db:
        page = await build_timeline(
            db, "run-tl-merge-1", kinds={"tool"}, since=None, until=None, limit=500, cursor=None
        )
    assert page.events
    assert {e.kind for e in page.events} == {"tool"}


@pytest.mark.asyncio
async def test_build_timeline_paginates_with_cursor():
    run_id = "run-tl-paginate-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)))
        for i in range(1200):
            db.add(
                AuditEntry(
                    idempotency_key=f"p-{i}",
                    run_id=run_id,
                    actor="lead",
                    action_kind="tool.bash",
                    status="ok",
                    started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc).replace(microsecond=i),
                )
            )
        await db.commit()

    seen_ids: set[tuple[str, str]] = set()
    cursor = None
    pages = 0
    async with async_session() as db:
        while True:
            page = await build_timeline(
                db, run_id, kinds={"tool"}, since=None, until=None, limit=500, cursor=cursor
            )
            pages += 1
            for ev in page.events:
                key = (ev.source, ev.source_id)
                assert key not in seen_ids
                seen_ids.add(key)
            if not page.has_more:
                break
            cursor = TimelineCursor.decode(page.next_cursor)
    assert pages == 3
    assert len(seen_ids) == 1200
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_build_timeline_merges_all_sources_in_order -q
```

Expected: `ImportError: cannot import name 'build_timeline'`.

- [ ] **Step 3: Implement `build_timeline`**

Append to `dashboard/backend/app/services/run_timeline.py`:

```python
import asyncio
import heapq

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
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_timeline.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): merge sources + cursor pagination (#387)"
```

---

## Task 7: Router endpoint — `GET /api/runs/{run_id}/timeline`

**Files:**
- Modify: `dashboard/backend/app/routers/runs.py`
- Test: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append failing test**

```python
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_timeline_endpoint_returns_page(client: AsyncClient):
    # Reuse `run-tl-merge-1` seeded earlier — tests run in order on shared db.
    resp = await client.get("/api/runs/run-tl-merge-1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-tl-merge-1"
    assert isinstance(body["events"], list)
    assert body["events"], "expected at least one event"
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_timeline_endpoint_rejects_unknown_kind(client: AsyncClient):
    resp = await client.get("/api/runs/run-tl-merge-1/timeline?kinds=lifecycle,bogus")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_endpoint_404_for_unknown_run(client: AsyncClient):
    resp = await client.get("/api/runs/run-does-not-exist/timeline")
    assert resp.status_code == 404
```

If `client` fixture is not yet present in this module, follow the pattern in `tests/test_runs.py` for instantiating it.

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_timeline_endpoint_returns_page -q
```

Expected: 404 (endpoint not wired) — test fails.

- [ ] **Step 3: Add the endpoint**

Insert into `dashboard/backend/app/routers/runs.py` immediately after `get_run_full_context` (around line 547):

```python
from app.services import run_timeline as run_timeline_service
from app.services.run_timeline import ALL_KINDS, TimelineCursor


def _parse_kinds(raw: str | None) -> set[str] | None:
    if raw is None or raw == "":
        return None
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = requested - ALL_KINDS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kinds: {sorted(unknown)}",
        )
    return requested


@router.get("/{run_id}/timeline", response_model=RunTimelinePage)
async def get_run_timeline(
    run_id: str,
    kinds: str | None = Query(None, description="Comma-separated subset of kinds"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RunTimelinePage:
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    decoded_cursor = TimelineCursor.decode(cursor) if cursor else None
    return await run_timeline_service.build_timeline(
        db,
        run_id,
        kinds=_parse_kinds(kinds),
        since=since,
        until=until,
        limit=limit,
        cursor=decoded_cursor,
    )
```

Ensure `RunTimelinePage`, `Query`, `HTTPException`, `datetime`, and `select`/`Run` are imported at the top of the file (most already are; add what's missing).

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/runs.py dashboard/backend/tests/test_run_timeline.py
git commit -m "feat(timeline): expose GET /api/runs/{run_id}/timeline (#387)"
```

---

## Task 8: Cursor stability under concurrent inserts

**Files:**
- Modify: `dashboard/backend/tests/test_run_timeline.py` (append)

- [ ] **Step 1: Append cursor-stability test**

```python
@pytest.mark.asyncio
async def test_cursor_stability_under_mid_stream_inserts():
    run_id = "run-tl-cursor-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)))
        for i in range(800):
            db.add(
                AuditEntry(
                    idempotency_key=f"cs-{i}",
                    run_id=run_id,
                    actor="lead",
                    action_kind="tool.bash",
                    status="ok",
                    started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc).replace(microsecond=i),
                )
            )
        await db.commit()

    async with async_session() as db:
        page1 = await build_timeline(
            db, run_id, kinds={"tool"}, since=None, until=None, limit=400, cursor=None
        )

    # Inject 100 new audit rows BEFORE page2 fetch — their timestamps fall
    # AFTER page1's last event's timestamp, so they belong on page2.
    last_ts = page1.events[-1].t
    async with async_session() as db:
        for i in range(100):
            db.add(
                AuditEntry(
                    idempotency_key=f"cs-late-{i}",
                    run_id=run_id,
                    actor="lead",
                    action_kind="tool.bash",
                    status="ok",
                    started_at=last_ts.replace(microsecond=last_ts.microsecond + 1 + i),
                )
            )
        await db.commit()

    cursor = TimelineCursor.decode(page1.next_cursor)
    async with async_session() as db:
        page2 = await build_timeline(
            db, run_id, kinds={"tool"}, since=None, until=None, limit=10_000, cursor=cursor
        )

    page1_keys = {(e.source, e.source_id) for e in page1.events}
    page2_keys = {(e.source, e.source_id) for e in page2.events}
    assert page1_keys.isdisjoint(page2_keys)
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_cursor_stability_under_mid_stream_inserts -q
```

Expected: 1 passed.

- [ ] **Step 3: (No implementation change)**

This test validates the cursor design from Task 6; it should pass without code changes.

- [ ] **Step 4: Run full suite**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py -q
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/test_run_timeline.py
git commit -m "test(timeline): cursor stability under mid-stream inserts (#387)"
```

---

## Task 9: Integration test against a synthetic run

**Files:**
- New: `dashboard/backend/tests/integration/test_run_timeline.py`

- [ ] **Step 1: Write the failing integration test**

Create `dashboard/backend/tests/integration/test_run_timeline.py`:

```python
"""End-to-end timeline test — at least one event per source per run (#387)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import async_session
from app.models import (
    AgentEvent,
    AuditEntry,
    ConflictResolution,
    CoordinatorTask,
    Run,
)
from app.services.run_timeline import build_timeline


@pytest.mark.asyncio
async def test_full_run_yields_every_source():
    run_id = "run-tl-integration-1"
    async with async_session() as db:
        db.add(Run(
            run_id=run_id,
            status="success",
            branch="feature/integration",
            started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 13, 15, 45, 0, tzinfo=timezone.utc),
            verdict="PR",
        ))
        db.add(AgentEvent(
            workflow_id="wfi",
            run_id=run_id,
            agent_id="manager",
            event_type="verdict_execute",
            event_data='{"verdict":"PR"}',
            created_at=datetime(2026, 5, 13, 15, 40, 0, tzinfo=timezone.utc),
        ))
        db.add(AuditEntry(
            idempotency_key="i-k1",
            run_id=run_id,
            actor="lead",
            action_kind="tool.bash",
            status="ok",
            started_at=datetime(2026, 5, 13, 15, 10, 0, tzinfo=timezone.utc),
        ))
        db.add(CoordinatorTask(
            id="i-t1",
            run_id=run_id,
            project_repo="x/y",
            status="completed",
            started_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
            claimed_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 13, 15, 15, 0, tzinfo=timezone.utc),
            teammate_agent_id="backend",
        ))
        db.add(ConflictResolution(
            branch="feature/integration",
            repo="x/y",
            phase_reached="llm",
            outcome="resolved",
            started_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 13, 15, 30, 30, tzinfo=timezone.utc),
        ))
        await db.commit()

    async with async_session() as db:
        page = await build_timeline(
            db, run_id, kinds=None, since=None, until=None, limit=500, cursor=None
        )

    kinds_seen = {e.kind for e in page.events}
    assert kinds_seen == {"lifecycle", "tool", "teammate", "verdict", "conflict"}
    times = [e.t for e in page.events]
    assert times == sorted(times)
    assert page.events[0].event == "run_start"
    assert page.events[-1].event in {"run_complete", "conflict.resolved", "verdict_execute"}
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/integration/test_run_timeline.py -q
```

Expected: 1 passed.

- [ ] **Step 3: (No code change)**

The integration test is a regression guard; it validates the wiring delivered by Tasks 1-7.

- [ ] **Step 4: Run full backend suite**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py tests/integration/test_run_timeline.py -q
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/integration/test_run_timeline.py
git commit -m "test(timeline): integration test covering every source (#387)"
```

---

## Task 10: Frontend — `TimelineTab.svelte`

**Files:**
- New: `dashboard/frontend/src/components/runs/TimelineTab.svelte`
- Modify: `dashboard/frontend/src/pages/RunDetail.svelte`

- [ ] **Step 1: Write the new tab component**

Create `dashboard/frontend/src/components/runs/TimelineTab.svelte`:

```svelte
<script lang="ts">
  type TimelineEvent = {
    t: string;
    kind: 'lifecycle' | 'tool' | 'teammate' | 'verdict' | 'conflict';
    event: string;
    source: string;
    source_id: string;
    agent: string | null;
    data: Record<string, unknown> | null;
  };

  type TimelinePage = {
    run_id: string;
    events: TimelineEvent[];
    next_cursor: string | null;
    has_more: boolean;
  };

  type Props = { runId: string };
  let { runId }: Props = $props();

  const ALL_KINDS: TimelineEvent['kind'][] = [
    'lifecycle', 'tool', 'teammate', 'verdict', 'conflict',
  ];

  let activeKinds = $state<Set<TimelineEvent['kind']>>(new Set(ALL_KINDS));
  let events = $state<TimelineEvent[]>([]);
  let cursor = $state<string | null>(null);
  let hasMore = $state(false);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let expanded = $state<Set<string>>(new Set());

  function kindParam(): string {
    if (activeKinds.size === ALL_KINDS.length) return '';
    return `&kinds=${[...activeKinds].join(',')}`;
  }

  async function loadPage(append: boolean) {
    loading = true;
    error = null;
    try {
      const cursorPart = append && cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
      const r = await fetch(
        `/api/runs/${runId}/timeline?limit=500${kindParam()}${cursorPart}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const page: TimelinePage = await r.json();
      events = append ? [...events, ...page.events] : page.events;
      cursor = page.next_cursor;
      hasMore = page.has_more;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  function toggleKind(k: TimelineEvent['kind']) {
    const next = new Set(activeKinds);
    if (next.has(k)) next.delete(k); else next.add(k);
    activeKinds = next;
    cursor = null;
    loadPage(false);
  }

  function toggleExpand(key: string) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    expanded = next;
  }

  $effect(() => { loadPage(false); });
</script>

<div class="timeline-tab">
  <div class="filters">
    {#each ALL_KINDS as k}
      <button
        class="chip"
        class:active={activeKinds.has(k)}
        onclick={() => toggleKind(k)}
      >{k}</button>
    {/each}
  </div>

  {#if error}
    <div class="error">Failed: {error}</div>
  {:else if events.length === 0 && !loading}
    <div class="empty">No events in this filter — try clearing it.</div>
  {:else}
    <ul class="events">
      {#each events as ev (ev.source + ':' + ev.source_id)}
        {@const key = ev.source + ':' + ev.source_id}
        <li class="event {ev.kind}">
          <button class="row" onclick={() => toggleExpand(key)}>
            <span class="t">{new Date(ev.t).toLocaleString()}</span>
            <span class="kind">{ev.kind}</span>
            <span class="ev">{ev.event}</span>
            {#if ev.agent}<span class="agent">{ev.agent}</span>{/if}
          </button>
          {#if expanded.has(key) && ev.data}
            <pre class="data">{JSON.stringify(ev.data, null, 2)}</pre>
          {/if}
        </li>
      {/each}
    </ul>
    {#if hasMore}
      <button class="load-more" disabled={loading} onclick={() => loadPage(true)}>
        {loading ? 'Loading…' : 'Load more'}
      </button>
    {/if}
  {/if}
</div>

<style>
  .timeline-tab { display: flex; flex-direction: column; gap: 0.75rem; }
  .filters { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .chip { padding: 0.15rem 0.5rem; border: 1px solid #444; border-radius: 999px; background: transparent; color: inherit; cursor: pointer; }
  .chip.active { background: #0e8a16; border-color: #0e8a16; color: #fff; }
  .events { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.15rem; }
  .event { border-left: 3px solid #444; padding-left: 0.5rem; }
  .event.lifecycle { border-color: #0e8a16; }
  .event.tool { border-color: #0075ca; }
  .event.teammate { border-color: #fbca04; }
  .event.verdict { border-color: #b60205; }
  .event.conflict { border-color: #d93f0b; }
  .row { display: flex; gap: 0.75rem; background: transparent; border: 0; padding: 0.25rem 0; color: inherit; text-align: left; cursor: pointer; width: 100%; font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .t { color: #888; min-width: 12em; }
  .data { background: #111; padding: 0.5rem; overflow: auto; font-size: 0.8rem; }
  .load-more { align-self: flex-start; padding: 0.4rem 0.9rem; }
  .error { color: #b60205; }
  .empty { color: #888; }
</style>
```

- [ ] **Step 2: Wire the tab into `RunDetail.svelte`**

In `dashboard/frontend/src/pages/RunDetail.svelte`:

1. Add `'timeline'` to the `TabId` union (around line 99):

```ts
  type TabId =
    | 'overview'
    | 'dag'
    | 'team'
    | 'conversation'
    | 'diff'
    | 'logs'
    | 'intelligence'
    | 'timeline';
```

2. In the `tabs` derivation (around line 130), push a timeline entry after `intelligence`:

```ts
    t.push({ id: 'timeline', label: 'Timeline' });
```

3. Import the new component at the top of the script:

```ts
  import TimelineTab from '../components/runs/TimelineTab.svelte';
```

4. Add a render block alongside the other `{:else if activeTab === '...'}` blocks (near line 831):

```svelte
      {:else if activeTab === 'timeline'}
        <section class="tab-pane">
          <TimelineTab runId={run.run_id} />
        </section>
```

- [ ] **Step 3: Run frontend type-check**

```bash
cd dashboard/frontend && npm run check
```

Expected: 0 errors, 0 warnings related to the new files.

- [ ] **Step 4: Smoke-test by hand**

```bash
cd dashboard/frontend && npm run dev
```

Open the dashboard, navigate to a run-detail page, click the **Timeline** tab. Expected: rows render, filter chips toggle, "Load more" works on a run with >500 events.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/runs/TimelineTab.svelte dashboard/frontend/src/pages/RunDetail.svelte
git commit -m "feat(timeline): Timeline tab on RunDetail with filter chips (#387)"
```

---

## Task 11: Playwright spec for the Timeline tab

**Files:**
- New: `dashboard/frontend/e2e/timeline.spec.ts`

- [ ] **Step 1: Write the spec**

Create `dashboard/frontend/e2e/timeline.spec.ts`:

```ts
import { expect, test } from '@playwright/test';

test('Timeline tab loads and filters', async ({ page }) => {
  await page.route('**/api/runs/run-canned/timeline*', async (route) => {
    const url = new URL(route.request().url());
    const kinds = url.searchParams.get('kinds');
    const allEvents = [
      { t: '2026-05-13T15:00:00Z', kind: 'lifecycle', event: 'run_start',
        source: 'runs', source_id: 'run-canned', agent: null, data: {} },
      { t: '2026-05-13T15:01:00Z', kind: 'tool', event: 'tool.bash.ok',
        source: 'audit_log', source_id: '1', agent: 'lead', data: { exit_code: 0 } },
    ];
    const filtered = kinds
      ? allEvents.filter((e) => kinds.split(',').includes(e.kind))
      : allEvents;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-canned',
        events: filtered,
        next_cursor: null,
        has_more: false,
      }),
    });
  });

  await page.goto('/runs/run-canned');
  await page.getByRole('button', { name: 'Timeline' }).click();
  await expect(page.locator('.event.lifecycle')).toBeVisible();
  await expect(page.locator('.event.tool')).toBeVisible();

  // Click the `tool` chip to deactivate it.
  await page.getByRole('button', { name: 'tool', exact: true }).click();
  await expect(page.locator('.event.tool')).toHaveCount(0);
  await expect(page.locator('.event.lifecycle')).toBeVisible();
});
```

- [ ] **Step 2: Run the spec**

```bash
cd dashboard/frontend && npx playwright test e2e/timeline.spec.ts
```

Expected: 1 passed.

- [ ] **Step 3: (No code change)**

Spec is a regression net.

- [ ] **Step 4: Run full e2e suite**

```bash
cd dashboard/frontend && npx playwright test
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/e2e/timeline.spec.ts
git commit -m "test(timeline): Playwright spec for filter + render (#387)"
```

---

## Task 12: Performance smoke + doc update

**Files:**
- Modify: `dashboard/backend/tests/test_run_timeline.py` (append)
- Modify: `docs/architecture.md` (append section)

- [ ] **Step 1: Append performance smoke test**

```python
import time


@pytest.mark.asyncio
async def test_timeline_first_page_under_500ms_for_10k_events():
    run_id = "run-tl-perf-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)))
        for i in range(10_000):
            db.add(
                AuditEntry(
                    idempotency_key=f"perf-{i}",
                    run_id=run_id,
                    actor="lead",
                    action_kind="tool.bash",
                    status="ok",
                    started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc).replace(microsecond=i % 1_000_000),
                )
            )
        await db.commit()

    async with async_session() as db:
        t0 = time.perf_counter()
        page = await build_timeline(
            db, run_id, kinds=None, since=None, until=None, limit=500, cursor=None
        )
        elapsed = time.perf_counter() - t0
    assert len(page.events) == 500
    assert elapsed < 0.5, f"first page took {elapsed*1000:.0f}ms (>500ms budget)"
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py::test_timeline_first_page_under_500ms_for_10k_events -q
```

Expected: 1 passed.

- [ ] **Step 3: Document the endpoint**

Append to `docs/architecture.md` under a new "## Run Timeline API" section:

```markdown
## Run Timeline API

`GET /api/runs/{run_id}/timeline` returns a chronologically merged event
stream for a single run, drawn from five sources:

| Source table | Kind | Notes |
|---|---|---|
| `runs` (+ `agent_events` with `event_type LIKE 'lifecycle.%'`) | `lifecycle` | `run_start` / `run_complete` synthesised from `started_at` / `finished_at`. |
| `audit_log` | `tool` | One event per row, `event` = `{action_kind}.{status}`. `data.stdout_tail` / `stderr_tail` trimmed to 1 KB. |
| `coordinator_tasks` | `teammate` | `teammate.spawned` at `claimed_at`, `teammate.completed` at terminal `updated_at`. |
| `agent_events` (`event_type IN verdict_execute, manager_review, manager_review_complete`) | `verdict` | Manager-decision events. |
| `conflict_resolutions` (matched by run's branch) | `conflict` | `conflict.started` + `conflict.{outcome}`. |

Pagination is cursor-based on `(t, source, source_id)`. Filter via `?kinds=`
(comma-separated subset). Full payloads remain available via
`/api/audit?run_id=…&id=…` for `tool` events whose tails are truncated.

Implementation: `dashboard/backend/app/services/run_timeline.py`.
```

- [ ] **Step 4: Run full backend + frontend suites**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py tests/integration/test_run_timeline.py -q
cd dashboard/frontend && npm run check
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/test_run_timeline.py docs/architecture.md
git commit -m "docs(timeline): document GET /api/runs/{run_id}/timeline (#387)"
```

---

## Task 13: Open PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feature/387-run-timeline-api
```

- [ ] **Step 2: Open a PR targeting `dev`**

```bash
gh pr create --base dev --title "feat: GET /api/runs/{run_id}/timeline + Timeline tab (#387)" --body "$(cat <<'EOF'
Closes #387.

## Summary
- New service `run_timeline.py` merges `runs`, `agent_events`, `audit_log`, `coordinator_tasks`, `conflict_resolutions` into one ordered stream.
- New endpoint `GET /api/runs/{run_id}/timeline` with `?kinds=`, `?since=`, `?until=`, `?limit=`, `?cursor=`.
- New Timeline tab on `RunDetail.svelte` with filter chips + "Load more".
- No new ingestion code; no schema changes; no migrations.

## Test plan
- [ ] `cd dashboard/backend && python3 -m pytest tests/test_run_timeline.py tests/integration/test_run_timeline.py -q`
- [ ] `cd dashboard/frontend && npx playwright test e2e/timeline.spec.ts`
- [ ] Hand-smoke: dashboard → Runs → pick a run → Timeline tab loads, filter chips toggle, Load more paginates.
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 3: (No code change)**

Wait for CI.

- [ ] **Step 4: (Operator step)**

Operator runs `/review <pr#>` per project conventions.

- [ ] **Step 5: (No commit)**

Done.

---

## Self-review checklist

- [x] Every acceptance criterion in `2026-05-14-issue-387-run-timeline-api.md` maps to ≥1 task:
  - Merged JSON across all five sources → Task 6 + Task 9.
  - `?kinds=` filter → Task 6 + Task 7 + Task 11.
  - Pagination → Task 6 + Task 8.
  - Frontend Timeline tab → Task 10 + Task 11.
  - No new ingestion / no schema changes → file structure shows zero `models.py` / migration touches.
- [x] No `TBD`, `TODO`, `add error handling`, `similar to Task N` placeholders.
- [x] Real paths verified: `dashboard/backend/app/routers/runs.py:437`, `dashboard/frontend/src/pages/RunDetail.svelte` `TabId` union, models at `dashboard/backend/app/models.py:44/118/246/277/292`.
- [x] Type / name consistency: `RunTimelineEvent`, `RunTimelinePage`, `TimelineCursor`, `build_timeline`, `ALL_KINDS` used identically across schema / service / router / tests.
