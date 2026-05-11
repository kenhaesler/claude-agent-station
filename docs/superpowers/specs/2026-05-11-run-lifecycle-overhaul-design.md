# Run Lifecycle Overhaul — Design

**Status**: design  
**Date**: 2026-05-11  
**Author**: dashboard analysis triage  
**Issues**: see "Issue tracker" section below — five companion issues track sub-deliverables.

## Context

Live inspection of the running stack (PR #341/#342/#343 era) revealed three intertwined UX problems on Mission Control:

1. Runs that have finished still appear "running" — even after the parent `Run` row is marked `completed`. The `/api/runs/active-employees` endpoint synthesizes phantom employees from stale `coordinator_tasks` rows.
2. After clicking *Trigger Run*, the operator stares at an unchanged dashboard for 5–30 s before any row appears. `run-manager.sh` fires `run_start` only after project enumeration / `gh issue list`.
3. A finished run keeps occupying Mission Control's chrome because `headerRunId` falls back to `latestRunId` with no idle transition.

Underneath, the dashboard has no per-run heartbeat, so the reaper waits up to 30 minutes before correcting stuck rows. And the bash orchestrator (`run-manager.sh`, 3192 LOC) is the structural reason most of these symptoms exist: EXIT-trap unreliability, no transactional finalizer for coordinator tasks, late webhook emission.

This spec covers five deliverables that address the symptoms (1–4) and begin a strangler-pattern migration of the bash orchestrator into Python (5).

## Goals

- The Mission Control "active" state matches the actual orchestrator state within seconds, not minutes.
- Trigger Run provides instant feedback (placeholder row visible immediately).
- Finished runs transition cleanly to an explicit idle state.
- Stuck/dead runs are visible and reaped on the order of seconds, not 15 s ticks + 30 min `unknown` windows.
- The orchestrator's lifecycle invariants (CoordinatorTask completion, webhook emission) live in a language that supports `try/finally` semantics.

## Non-goals

- Rewriting `integration-branch.sh`, `promote.sh`, `resolve-conflicts.sh`, `circuit-breaker.sh`. These are standalone scripts called by cron / dashboard / manually; their bash form is stable.
- Migrating `sprint-cycle.sh`. Specialized, rarely fired.
- Changing webhook payloads or `run_id` semantics. Backwards-compatibility constraint on the dashboard.
- Async overhaul. Synchronous `subprocess` calls in Python first; consider `asyncio` only if profiling demands it.

## Deliverables

Five items, five separate PRs, in approximate ship order. Items 1, 2, 3, 4 each ship independently; item 5 is itself split into three sub-PRs (5a, 5b, 5c).

### Item 1 — Zombie CoordinatorTask cleanup (hotfix)

**Problem**: `run_lifecycle.handle_finished()` finalizes `Run.status` but never updates `coordinator_tasks`. `get_active_employees()` synthesizes phantom running entries from those stale rows.

**Fix**:

1. In `handle_finished()`, after `run.status = final_status`, issue:
   ```sql
   UPDATE coordinator_tasks
   SET status = 'orphaned', claimed_at = NULL
   WHERE run_id = :run_id AND status IN ('claimed', 'running')
   ```
   `orphaned` is a new status value distinguishing "we don't know how this ended" from `completed`/`failed`. Documented in the CoordinatorTask docstring.

2. In `get_active_employees()` (`routers/runs.py:131`), guard the coordinator-task synthesis fallback: skip synthesis when the parent `Run.status` is terminal (`completed`/`failed`/`interrupted`).

**Tests**: pytest covering both — webhook cascades; active-employees doesn't resurrect zombies.

**Estimated size**: ~30 LOC + ~60 LOC tests. Single PR; targets `dev`.

### Item 2 — Optimistic run placeholder on trigger

**Problem**: `run-manager.sh` fires `run_start` only after project enumeration (~5–30 s after click).

**Fix**: backend-only optimistic SSE pattern.

1. `routers/runs.py::trigger_run` generates the `run_id` (`run-{utc-timestamp}`) server-side and:
   - Inserts `Run(run_id=…, status="pending", started_at=now)` row.
   - Publishes `run_start` event with `status="pending"` to event_bus → SSE → frontend refresh.
   - Calls launcher with `{"hint_run_id": run_id}`.
2. `agent/launcher.py` accepts the hint, passes it to `run-manager.sh` as `STATION_RUN_ID_OVERRIDE` env var.
3. `run-manager.sh:27` adopts the override when present (`RUN_ID="${STATION_RUN_ID_OVERRIDE:-$(date -u +%Y%m%dT%H%M%SZ)}"`).
4. When bash later fires real `run_start`, `handle_started()` finds the existing row and upgrades `status` to `running` (existing logic at `run_lifecycle.py:137` already handles this).

**Edge cases**:
- Launcher returns 409 "already running" → mark placeholder `status='superseded'`, emit `run_complete`.
- Launcher unreachable → mark placeholder `status='failed'`, emit `run_complete`.
- User closes tab → placeholder remains; reaper handles stale `pending` rows after `PENDING_REAP_AGE_SECONDS` (default 90 s, configurable).

**Tests**: pytest for the `pending`→`running` transition, the 409 path, the unreachable-launcher path.

**Estimated size**: ~60 LOC backend + ~10 LOC bash + ~80 LOC tests. Single PR; targets `dev`. Depends on item 4 only loosely (it stands alone but pairs well with the heartbeat for UI polish).

### Item 3 — Mission Control idle state

**Problem**: `MissionControl.svelte:26` falls back to `latestRunId` when `activeRuns` empties; the chrome continues binding to the just-finished run.

**Fix**: explicit idle render branch.

1. New `dashboard/frontend/src/components/mission/IdlePanel.svelte` (~80 LOC):
   - Shows last run's verdict, finished_at as relative time.
   - "View details" link to the run detail page.
   - Prominent "Trigger Run" button (the existing handler).
   - A subtle "✓ Last run finished N s ago" status.
2. `MissionControl.svelte`: top-level `{#if activeRuns.length > 0}…existing chrome…{:else}<IdlePanel lastRun={recentRuns[0] ?? null} />{/if}`.
3. Live event panel hides when idle.

**Synergy with item 2**: optimistic `pending` runs count as active, so the idle panel stays hidden during the click→spawn transition. No race window.

**Tests**: existing svelte-check passes; manual UI verification in the running container.

**Estimated size**: ~100 LOC. Single PR; targets `dev`.

### Item 4 — `Run.last_event_at` heartbeat

**Problem**: No way to distinguish "alive and busy" from "died silently." Reaper waits up to 30 min on `unknown` rows.

**Fix**: per-run heartbeat column updated by every webhook.

1. DB: new column `runs.last_event_at DATETIME` (nullable for legacy rows). Migration entry in `_migrate_add_columns`. Index for the reaper.
2. `routers/webhook.py`: at the single point where every event is processed and resolved to a `Run` row, set `run.last_event_at = datetime.now(timezone.utc)`. This covers narration, progress_update, task_*, lifecycle events.
3. `services/stale_run_reaper.py`: new criterion — `running` rows whose `last_event_at` is older than `_ACTIVE_HEARTBEAT_TIMEOUT_SECONDS` (default 120 s) AND `service_active==False` get reaped immediately, bypassing the 30-min `unknown` window.
4. API: `RunOut` and `ActiveEmployeeOut` include `last_event_at`.
5. Frontend: `Run` type extended; Mission Control renders an "active N s ago" badge near the run header — neutral until 60 s, amber to 180 s, red after.

**Tests**:
- pytest: webhook bumps `last_event_at`; reaper acts on stale heartbeat.
- Manual: trigger run, observe badge.

**Estimated size**: ~60 LOC + ~80 LOC tests. Single PR; targets `dev`.

### Item 5 — First milestone of bash → Python migration

**Problem**: ~5000 LOC of orchestration bash. The structural source of webhook-reliability bugs, no `try/finally` semantics, hard to test, hard to evolve.

**Strangler pattern in three sub-PRs**. Each sub-PR delivers value independently and can be reverted in isolation.

#### Sub-PR 5a — Webhook emission to Python helper

- New `agent/webhook_emitter.py`: synchronous client. Public functions:
  - `emit(event: str, run_id: str, payload: dict) -> None`
  - Convenience wrappers: `emit_run_start`, `emit_run_complete`, `emit_employee_start`, `emit_employee_complete`, `emit_verdict_execute`, `emit_manager_review`, etc.
  - Reads dashboard URL + webhook secret from env (matches existing `STATION_WEBHOOK_URL` / `STATION_WEBHOOK_SECRET`).
  - Retries with exponential backoff (3 attempts, 0.5s/1s/2s) on 5xx and connection errors. Logs structured JSON.
- Bash `webhook_event()` becomes `python3 -m agent.webhook_emitter <event> --run-id $RUN_ID --json '{…}'`.
- All ~30 `webhook_event` call sites in `run-manager.sh` continue to work unchanged.
- **Win**: one code path, with retries. No more "lost EXIT-trap webhook."

**Estimated size**: ~150 LOC Python + ~10 LOC bash delta + ~80 LOC tests.

#### Sub-PR 5b — CoordinatorTask lifecycle to Python

- New `agent/coordinator_lifecycle.py`: tiny module wrapping the dashboard's `/api/coordinator/tasks` HTTP API (or direct DB calls if HTTP layering is too noisy — defer that decision to implementation).
- Public functions: `create_task`, `claim_task`, `update_progress`, `complete_task`, `fail_task`.
- Bash's `queue_api POST …` calls for coordinator tasks are replaced; bash continues to invoke this module via `python3 -m agent.coordinator_lifecycle …`.
- The module owns the try/finally: when a task is created in `create_task`, it registers a stack frame; `complete_task` or `fail_task` clears it; if the process exits before clearing, an atexit handler calls `fail_task` with reason='orphaned'.
- **Win**: structural guarantee that coordinator tasks always reach a terminal state. Complements item 1.

**Estimated size**: ~200 LOC Python + ~30 LOC bash delta + ~100 LOC tests.

#### Sub-PR 5c — Project loop to Python

- New `agent/project_loop.py`: the per-project iteration today living in `run-manager.sh` between roughly line 1700 and line 2700. Calls into `station_orchestrator.py` for the SDK work.
- `run-manager.sh` becomes a thin shim (~200 LOC: env setup, GH_TOKEN export, log redirect, lock acquisition, `exec python3 -m agent.station_orchestrator …`).
- The existing `station_orchestrator.py` is extended with a `RunDriver` class that owns the full run lifecycle.
- The driver wraps the entire run in a `try/finally` that guarantees `emit_run_complete` fires, even on uncaught exceptions.
- **Win**: end of EXIT-trap dependency. The orchestrator can finally finalize.

**Estimated size**: ~300 LOC Python + ~2500 LOC bash deletions + ~150 LOC tests.

#### Cross-cutting design decisions

- **Compat**: `run_id` semantics unchanged. Webhook payloads unchanged. Dashboard requires zero changes.
- **Logging**: Python writes to the same `/var/log/claude-agent/run-<RUN_ID>-launcher.out` log file the bash writes to (line-mode, no interleaving issues because we exec rather than fork-and-pipe). Operators see one log stream.
- **Subprocess invocation**: `gh`, `git`, `claude` still invoked via `subprocess.run()` from Python.
- **Concurrency**: bash uses `&` for background jobs (multi-employee spawn). Python equivalent is `concurrent.futures.ThreadPoolExecutor`. Synchronous first; convert to asyncio only if profiling justifies.

#### Risks and mitigations

- **Subprocess error-semantics drift between bash and Python**. Mitigation: a thin `bash_compat.run()` wrapper that mirrors `[ $? -eq 0 ]` checks one-for-one. Reviewed against bash call sites in PR review.
- **Subtle bash quoting**. Mitigation: golden-file tests for `gh` command construction; review for `shlex.quote` parity.
- **OAuth token refresh logic in bash**. Mitigation: 5b moves this alongside coordinator task lifecycle (shared auth dep).

#### Phasing

5a → 5b → 5c. 5a and 5b are mostly independent (different files, different bash call sites) and can land in parallel if reviewer capacity permits. 5c depends on both.

## Issue tracker

Five GitHub issues to be created before implementation:

| Issue | Title | Scope |
|---|---|---|
| #N1 | fix(runs): orphan stale coordinator_tasks when parent run finishes | Item 1 |
| #N2 | feat(runs): optimistic placeholder on Trigger Run | Item 2 |
| #N3 | feat(mission-control): explicit idle state when no active run | Item 3 |
| #N4 | feat(runs): last_event_at heartbeat + reaper integration | Item 4 |
| #N5 | refactor(agent): migrate run-manager.sh orchestration to Python (milestone 1) | Item 5; tracks sub-PRs 5a/5b/5c |

Each PR references its issue with `Fixes #N`.

## Testing strategy

- **Unit (pytest)**: each backend change ships with a test that exercises the new path AND the negative path (e.g., zombie cleanup verified by inserting stale rows and asserting cleanup; webhook emitter verified against a mock httpx client).
- **Integration**: the existing `tests/test_run_lifecycle.py` is the canonical end-to-end fixture. We extend it for the new states (`pending`, `orphaned`).
- **Manual UI verification**: items 2, 3, and 4 ship with a manual test checklist run in the live container (rebuild → trigger run → observe).
- **Bash regression**: 5a/5b/5c each include a bash-shim parity test comparing before/after webhook payloads against a recorded golden file.

## Rollout

Each item lands on `dev`; user promotes to `main` per project conventions. No feature flags — the changes are individually small and revertible per-PR.

For item 5: each sub-PR is independently shippable. The bash shim continues to work after 5a alone; after 5b alone; etc. The "big bang" is avoided by construction.

## Open questions

None at design time. Implementation will surface specifics (e.g., direct-DB vs HTTP for `coordinator_lifecycle.py`); those decisions are local to a sub-PR and don't reshape the design.
