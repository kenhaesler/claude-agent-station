# Unified Run Timeline API — Design

**Status**: design
**Date**: 2026-05-14
**Author**: tier-2-architect
**Issue**: [#387](https://github.com/kenhaesler/claude-agent-station/issues/387) — *Tier 2 / Issue B* of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)

## Context

During the PR #381 investigation, understanding a single run required grepping six places: per-run log files, the manager stream JSONL, the `audit_log` table, the `runs` table, the dashboard webhook log, and bundled SDK CLI stream files. Operators can't realistically reconstruct a run from that surface. The dashboard already renders pieces of this — `routers/runs.py::get_run_full_context` (line 437) returns a Run plus its coordinator tasks and events; `routers/audit.py::list_audit_entries` returns tool-call telemetry; `routers/agent_events.py` returns lifecycle events — but nothing merges them in time order on the backend.

The underlying data is already well-modeled and indexed:

- `runs` (`models.py:44`) — one row per run; lifecycle status transitions are reconstructible from `started_at` / `finished_at` / `verdict` / `last_event_at`.
- `agent_events` (`models.py:277`) — append-only event log, indexed by `run_id`. Lifecycle and workflow transitions.
- `audit_log` (`models.py:246`) — per-tool-call telemetry, indexed by `run_id`. Bash / Edit / Read / etc. with status, exit code, stdout/stderr tails.
- `coordinator_tasks` (`models.py:118`) — teammate task DAG, indexed by `run_id`. Captures teammate spawn / claim / complete moments.
- `Run.verdict` + `verdict_detail` — terminal manager decision.
- `conflict_resolutions` (`models.py:292`) — optional pre-merge conflict-resolution episodes scoped by `run_id`.

The boundary between `agent_events` and `audit_log` is documented at `models.py:246-258`. `agent_events` records orchestration decisions; `audit_log` records actions executed. They are complementary, not duplicative — both belong in the timeline.

This issue adds one endpoint — `GET /api/runs/<run_id>/timeline` — that JOINs across these sources and returns a chronologically merged event list. No new ingestion code. The frontend gets a new "Timeline" tab on the run detail page. The "grep six places" workflow goes away.

## Goals

- Provide a single backend endpoint that returns every event for a run, ordered by time, with a unified envelope.
- Support filter by `kind` (lifecycle, tool, teammate, verdict, conflict) and pagination for runs with >1000 events.
- Render a Timeline tab on the existing run detail page that consumes this endpoint.
- Zero changes to data ingestion — pure query layer.

## Non-goals

- New ingestion / new tables. Every source already exists.
- Real-time push to the timeline (SSE). The existing per-run SSE stream already covers live runs; the timeline is a "look back" view.
- Cross-run timelines / global activity feeds. Out of scope.
- Replacing the existing audit / events / runs endpoints. Timeline composes them; it does not deprecate them.
- Authoring new event kinds. The five sources listed above cover the needed surface today.

## Approach

### Endpoint contract

`GET /api/runs/{run_id}/timeline`

Query params:

- `kinds` — comma-separated list, defaults to all. Allowed values: `lifecycle`, `tool`, `teammate`, `verdict`, `conflict`.
- `since` — ISO-8601 timestamp, optional. Inclusive lower bound.
- `until` — ISO-8601 timestamp, optional. Exclusive upper bound.
- `limit` — default 500, max 5000.
- `cursor` — opaque, returned in the previous page's response.

Response:

```json
{
  "run_id": "run-20260513T151408Z",
  "events": [
    {"t": "2026-05-13T15:14:08Z", "kind": "lifecycle", "event": "run_start",
     "source": "runs", "source_id": 12345, "agent": null, "data": {...}},
    {"t": "2026-05-13T15:19:05Z", "kind": "teammate", "event": "spawned",
     "source": "coordinator_tasks", "source_id": "task-run-...-1",
     "agent": "backend", "data": {"task_id": "...", "title": "..."}},
    {"t": "2026-05-13T15:19:23Z", "kind": "tool", "event": "tool.bash.ok",
     "source": "audit_log", "source_id": 98765,
     "agent": "teammate-backend", "data": {"exit_code": 0,
     "stdout_tail": "...", "duration_ms": 423}}
  ],
  "next_cursor": "eyJ0Ijo...",
  "has_more": false
}
```

The envelope's `kind` is the discriminator; `data` carries the source-specific payload verbatim from the underlying row's columns. `source` + `source_id` are present so the frontend can deep-link to the per-source detail view (run-detail audit tab, agent-events tab, etc.).

### Where the code lives

New service: `dashboard/backend/app/services/run_timeline.py`. The router (`routers/runs.py`) only orchestrates the HTTP shape; the merge logic stays in the service so unit tests can exercise it without an HTTP client.

```python
async def build_timeline(
    db: AsyncSession,
    run_id: str,
    *,
    kinds: set[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 500,
    cursor: TimelineCursor | None = None,
) -> TimelinePage:
    ...
```

Returns `TimelinePage(events: list[TimelineEvent], next_cursor: TimelineCursor | None, has_more: bool)`.

### Router wiring

`dashboard/backend/app/routers/runs.py` — new endpoint added near `get_run_full_context` (line 437):

```python
@router.get("/{run_id}/timeline", response_model=RunTimelinePage)
async def get_run_timeline(
    run_id: str,
    kinds: str | None = Query(None, description="Comma-separated kinds filter"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _run_exists(db, run_id)
    return await run_timeline.build_timeline(
        db, run_id,
        kinds=_parse_kinds(kinds),
        since=since, until=until,
        limit=limit, cursor=TimelineCursor.decode(cursor) if cursor else None,
    )
```

Schemas (`app/schemas.py`) gain `RunTimelinePage`, `RunTimelineEvent`. `RunTimelineEvent.data` is `dict | None` so the route is dialect-agnostic between SQLite (JSON-as-text) and Postgres (JSONB).

### Merge strategy

The naive approach is one query per source, then merge in Python. At 1000-event runs that's five `SELECT … WHERE run_id = ?` queries — each indexed — followed by a heap-merge in Python. Cheap enough.

For very large runs (>5000 events), a UNION ALL query in SQL is the right shape, but the union approach forces the column shapes to align across sources. The Python heap-merge keeps the per-source projection clean. Both approaches preserve the indexed `run_id`-scoped lookup.

Pseudocode:

```python
async def build_timeline(db, run_id, *, kinds, ...):
    sources = []
    if "lifecycle" in kinds:
        sources.append(_lifecycle_events(db, run_id, since, until))
    if "tool" in kinds:
        sources.append(_audit_events(db, run_id, since, until))
    if "teammate" in kinds:
        sources.append(_teammate_events(db, run_id, since, until))
    if "verdict" in kinds:
        sources.append(_verdict_events(db, run_id, since, until))
    if "conflict" in kinds:
        sources.append(_conflict_events(db, run_id, since, until))
    streams = await asyncio.gather(*sources)
    merged = heapq.merge(*streams, key=lambda e: (e.t, e.source, e.source_id))
    return _paginate(merged, limit, cursor)
```

Each `_*_events` returns a list ordered by `t` so `heapq.merge` is correct. Per-source projections:

- **`_lifecycle_events`**: synthesizes from `runs` columns. Emits `run_start` at `started_at`, `run_complete` at `finished_at`, plus AgentEvent rows whose `event_type` starts with `lifecycle.` (e.g. `lifecycle.orchestrator_complete`).
- **`_audit_events`**: `SELECT * FROM audit_log WHERE run_id = ? AND started_at BETWEEN ?, ?`. Emits one event per row at `started_at`; the row's `status` distinguishes `tool.<kind>.started` from `.ok` / `.error` / `.timeout`. (If both phases are wanted as separate events, two emissions per row — `started_at` and `finished_at`. The frontend prefers two; the first sketch ships with one and follows up.)
- **`_teammate_events`**: `SELECT * FROM coordinator_tasks WHERE run_id = ?`. Emits `teammate.spawned` at `claimed_at`, `teammate.completed` at the row's terminal `updated_at` (or `finished_at` if present), with `agent=teammate_agent_id`.
- **`_verdict_events`**: pulls from `agent_events` where `event_type IN ('verdict_execute', 'manager_review', 'manager_review_complete')`.
- **`_conflict_events`**: `SELECT * FROM conflict_resolutions WHERE run_id = ?`. Emits `conflict.started` at `started_at` and `conflict.<outcome>` at `finished_at`.

### Pagination

Cursors encode `(t, source, source_id)` of the last-emitted event so the next page is `WHERE (t, source, source_id) > (last_t, last_source, last_source_id)`. Base64-encoded JSON. Stable across same-second events because the tie-break key includes source + source_id.

`has_more` is computed by reading `limit + 1` and trimming.

### Frontend

`dashboard/frontend/src/routes/runs/[run_id]/+page.svelte` already has tabs (Logs, Diff, Coordinator, Events). A fifth tab "Timeline" is added.

The Timeline tab component (`dashboard/frontend/src/lib/components/runs/TimelineTab.svelte`, ~200 LOC):

- Fetches `/api/runs/<run_id>/timeline` with a default `?limit=500`.
- Renders a vertical list grouped by minute, each row showing `t`, an icon by `kind`, a one-line summary, and an expand toggle for `data`.
- "Load more" button uses `next_cursor`.
- Filter chips toggle `?kinds=`. URL syncs the filter for shareable links.
- Empty state: "No events in this filter — try clearing it."

### Existing endpoint deprecation

`get_run_full_context` keeps shipping — it's used by other tabs for non-timeline projections (the verdict block, the diff fetch). The timeline endpoint does not replace it.

## Acceptance criteria

From the issue body, expanded:

- [ ] **`GET /api/runs/<run_id>/timeline` returns merged JSON.** All five sources merged, ordered by time, with `kind`, `source`, `source_id`, and source-specific `data` payload. Verified by a fixture run with at least one row per source.
- [ ] **Configurable filter (`?kinds=lifecycle,verdict`).** Comma-separated list parsed server-side; unknown kinds rejected with 400. Default = all kinds.
- [ ] **Pagination for runs with >1000 events.** `limit` + `cursor` honored; `next_cursor` returned when `has_more=true`. Cursor stability tested under concurrent ingestion (later events arriving mid-pagination must not duplicate already-emitted rows).
- [ ] **Frontend: Timeline tab on run detail page.** Visible in the run-detail UI as the fifth tab. Renders kinds with distinct icons / colors. Filter chips wired to `?kinds=`.
- [ ] **No new ingestion code — purely a JOIN across existing tables.** Verified by review: no INSERT/UPDATE statements in the new code; no schema changes; no migrations.

## Dependencies / Blocks

- **Independent** of [[2026-05-14-issue-388-approve-integration-verdict]] (consumes the new verdict literal naturally via `agent_events.event_type='verdict_execute'`, no code change needed).
- **Independent** of [[2026-05-14-issue-386-per-project-containers]] — works on single-container or multi-container runtime equally.
- **Loose dependency** on [[2026-05-14-issue-393-postgres-migration]] — under Postgres' JSONB, `data` payloads come back as dicts directly; under SQLite, they're JSON-text. The endpoint handles both via a small `_decode_json` helper. Shipping #387 before #393 means a single dialect check in one helper; shipping after #393 lets it drop. Either order works.
- **Blocks** the Tier 3 work to merge live SSE + timeline ([[issue-391-run-decomposition]] follow-up) — that builds on the timeline envelope. Not in scope here.

## Risks and rollback

- **Performance on huge runs.** A run with 10 000 audit rows + 2 000 events + N teammates is unusual but possible. Mitigation: each source query is bounded by the `run_id` index (already present); the in-Python heap-merge is O(N log K) with K=5 streams. Cursor-based pagination keeps response sizes bounded. Add `EXPLAIN QUERY PLAN` smoke test to ensure no source query falls into a table scan.
- **Cursor instability under ingestion races.** New audit rows landing between page 1 and page 2 could appear out of cursor order if `started_at` ties happen to fall the wrong way. Mitigation: the tie-break key includes `source` and `source_id` so ordering is total; under SQLite the tie-break collapses to insertion order via the integer PK, which is monotonically increasing per-source.
- **Source-payload bloat.** `audit_log.stdout_tail` / `stderr_tail` can be hundreds of KB. Returning them inline in `data` for every tool event makes timeline responses heavy. Mitigation: `data` for `tool` events trims to first 1 KB + a `truncated: true` flag; full payload remains available via `/api/audit?run_id=…&id=…`.
- **JSON-text vs JSONB drift.** Pre-Postgres, `agent_events.event_data` is `Text` with JSON; post-Postgres it's JSONB. Mitigation: one `_decode_json` helper; one test parametrized across both backends.
- **Rollback**: single endpoint, single service module, one frontend tab. Reverting is one PR; no schema and no data implications.

## Test strategy

- **Unit (`tests/test_run_timeline.py`)**: build a fixture with one row in each source table; assert merged ordering, count, kinds present. Parametrize filter (`kinds=lifecycle` only — assert others absent).
- **Unit pagination**: insert 1200 audit rows; iterate the endpoint with `limit=500`; assert exactly 3 pages, no duplicates, no gaps.
- **Cursor stability**: page 1 reads `limit=500`; insert 100 rows mid-test; page 2 reads with the cursor; assert no duplicate `(source, source_id)` between pages.
- **Integration (`tests/integration/test_run_timeline.py`)**: trigger a synthetic run end-to-end; fetch the timeline; assert every observed source produced at least one event; assert `run_start` precedes `run_complete`.
- **Frontend (`dashboard/frontend/tests/timeline.spec.ts`)**: render the tab with a canned response; assert the filter chips toggle the kinds; assert "Load more" calls the cursor.
- **Performance smoke**: a generated 10 000-event run; assert the first page returns under 500 ms locally.

## Notes

- The issue body's bullet "No new ingestion code — purely a JOIN across existing tables" is honored exactly. The five sources line up to existing tables with existing `run_id` indexes; the work is projection + ordering + envelope.
- `coordinator_tasks` lacks a dedicated `finished_at` column today; the lifecycle endpoint uses `updated_at` when the row's `status` becomes terminal. A follow-up could add `finished_at` for cleaner semantics, but that's out of scope here.
- `routers/audit.py::list_audit_entries` already exposes audit rows scoped by `run_id`; the timeline service depends on the same indexed query path (`AuditEntry.run_id` indexed at `models.py:265`). No new index needed.
- The timeline envelope intentionally mirrors the shape proposed in the issue body. The only addition is `source` + `source_id` for deep-linking.
