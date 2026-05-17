# Idle-Run Semantics — Design

**Issues:** #446 (status taxonomy), #447 (event taxonomy)
**Scope:** Single PR covering both.
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-17

## Problem

The orchestrator cannot distinguish two terminal conditions that share a code path today:

1. **Idle** — the agent enumerated projects, found no eligible work, and exited cleanly.
2. **Real failure** — the agent ran work, but the manager produced no verdicts (crashed, hit max-turns, never spawned, etc.).

Both currently surface as `runs.status="failed"` plus `skip_reason="no-eligible-issues-proposals-pending"`, and both emit `manager_no_verdicts`. Consequences:

- Dashboard runs list cannot visually separate idle passes from genuine errors.
- Any failure-rate metric over `runs.status` is inflated by idle passes (likely the majority once the issue queue drains).
- `manager_no_verdicts` consumers (dashboard, queue, ops alerting) cannot distinguish "manager ran and failed" from "no manager ever ran".

Live evidence: run `run-20260516T205311Z` (5 s, no eligible issues) recorded `status="failed"` and fired `manager_no_verdicts` despite no manager being spawned.

## Goals

- `runs.status="skipped"` for idle runs; reserve `failed` for genuine errors.
- New webhook event `project_skipped_no_work` for the idle per-project case; preserve `manager_no_verdicts` exclusively for the real-failure case.
- Dashboard renders `skipped` distinctly from `failed`.
- Backwards-compatible at the data layer (no migration of historical rows).

## Non-Goals

- No DB migration / backfill of historical `failed + skip_reason=...` rows.
- No rename of `manager_no_verdicts`.
- No restructuring of the verdicts-file check into an explicit state machine.
- No new `skip_reason` values — existing values are reused.

## Discriminator

A new bool `work_attempted` returned by `agent.station_orchestrator.orchestrate_project`. Semantics:

- `True` — the lead entered an SDK session with eligible work and spawned teammates and/or a manager.
- `False` — the project had no eligible issues; the lead exited before spawning any teammate or manager.

All non-idle exit paths (orchestrator exception, teammate work attempted, manager spawned) → `True`. The default on any uncertainty is `True` — the safe choice is to preserve the existing failure signal, not to suppress it.

**Detection point.** `orchestrate_project` already determines, before opening the SDK session, whether any eligible issues exist for the project (via the issue picker / vision bootstrap). The exact decision site is implementation-defined and should be identified during the implementation plan; the contract is simply that `work_attempted` reflects whether the picker returned at least one eligible item and the SDK session was therefore opened. If the picker returned items but the SDK session immediately failed to open (e.g. provider auth error), that is `work_attempted=True` (work was attempted; it failed) — preserving the failure signal per the safe-default rule.

## Signal Flow

### `agent/station_orchestrator.py::orchestrate_project`
Return changes from `(rc, state)` to `(rc, state, work_attempted)`.

### `agent/project_loop.py::iterate_projects` per-project branch
Unpack the 3-tuple. Branch BEFORE the verdicts-file check:

```python
proj_rc, proj_state, work_attempted = asyncio.run(
    orchestrate_project(project, config, run_id, workspaces_dir)
)

if not work_attempted:
    # Idle: emit project_skipped_no_work, append SKIP result, do NOT
    # bump exit_code, do NOT read verdicts, do NOT emit manager_no_verdicts.
    try:
        from agent.webhook_emitter import emit as _emit_skip
        _emit_skip(
            "project_skipped_no_work",
            run_id=f"run-{run_id}",
            payload={
                "project": project.get("repo", ""),
                "reason": "no_eligible_work",
            },
        )
    except Exception:  # noqa: BLE001 — best-effort signal
        logger.exception("project_skipped_no_work webhook emit failed")

    results.append({
        "project": project.get("repo", ""),
        "decision": "SKIP",
        "reason": "no_eligible_work",
    })
    continue

# Existing flow: read verdicts file, emit manager_no_verdicts on miss
# (real failure), iterate verdicts on hit. Unchanged.
```

### Run-level terminal status (`iterate_projects` epilogue)

Track two flags across the per-project loop:

- `any_work_attempted: bool = False` — set `True` on any project that returns `work_attempted=True`.
- `any_real_failure: bool = False` — set `True` on any orchestrator exception, missing verdicts after work, executor error, or non-zero `exit_code` bump from existing paths.

Run terminal status logic:

- `any_real_failure` → existing status mapping (`failed`, etc.).
- `not any_work_attempted and not any_real_failure` AND at least one project enumerated → `"skipped"`.
- `any_work_attempted and not any_real_failure` → existing `completed` mapping.

The state-emit that closes the run sends `status="skipped"` for the new case. `skip_reason` is preserved unchanged.

### `dashboard/backend/app/services/run_lifecycle.py:153-163` status map
Add one entry:

```python
EVENT_STATUS_MAP = {
    "success": "completed",
    "finished": "completed",
    "no_reports": "completed",
    "completed": "completed",
    "rate_limited": "completed",
    "skipped": "skipped",       # NEW
    "error": "failed",
    "interrupted": "interrupted",
}
```

### `dashboard/backend/app/routers/webhook.py`
Route `project_skipped_no_work` to its handler. The existing webhook dispatch normalizes by event name — add the handler entry alongside `manager_no_verdicts`.

### Frontend (`dashboard/frontend/src/`)
- `src/lib/types.ts` — extend the status union to include `"skipped"`; add `"project_skipped_no_work"` to the event-name union.
- All pages that render run status (`CommandCenter.svelte`, `MissionControl.svelte`, `ProjectsPage.svelte`, `ProjectDetail.svelte`, `RunDetail.svelte`, `AgentTeamsCanvas.svelte`) — add a `skipped` case to the status-badge mapping. Color: neutral grey (distinct from `failed` red and `completed` green).

### `docs/architecture.md`
- Add `skipped` to the run-status table.
- Add `project_skipped_no_work` to the webhook-events list with the same row schema as the other events.

## Error Handling

- `project_skipped_no_work` emit wrapped in `try/except Exception` + `logger.exception(...)` — same pattern as the existing `plan_review_start` and `manager_no_verdicts` emits (consistency from #444 / #445 hygiene work).
- If `orchestrate_project` raises, `work_attempted` is inferred to be `True` (existing error path runs). This preserves the failure signal on the safe side.
- If a project returns `work_attempted=False` BUT a verdicts file exists on disk (defensive), the verdicts file is ignored and the project is still classified `skipped`. This is contradictory state we don't expect; logging a warning is sufficient.

## Testing

### Backend (`dashboard/backend/tests/`)

1. **`test_iterate_projects_emits_project_skipped_no_work_when_no_work_attempted`** — mock `orchestrate_project` to return `(0, None, False)`. Assert:
   - `webhook_emitter.emit` called with `("project_skipped_no_work", run_id="run-...", payload={"project": ..., "reason": "no_eligible_work"})`.
   - `webhook_emitter.emit` NOT called with `"manager_no_verdicts"`.
   - Result entry has `decision == "SKIP"` and `reason == "no_eligible_work"`.
   - `exit_code` unchanged from its pre-call value.

2. **`test_iterate_projects_still_emits_manager_no_verdicts_for_real_failure`** — mock `orchestrate_project` to return `(0, None, True)`; mock verdicts file to be absent. Assert existing `manager_no_verdicts` behavior preserved (kwargs shape, `exit_code=6`, `decision="ERROR"`). Regression pin so the `work_attempted` discriminator doesn't accidentally suppress the failure signal.

3. **`test_iterate_projects_sets_run_level_skipped_when_all_projects_skipped`** — all enabled projects return `work_attempted=False`. Assert run's final state-emit carries `status="skipped"`.

4. **`test_iterate_projects_does_not_set_skipped_when_any_project_did_work`** — mixed: one `work_attempted=False`, one `work_attempted=True` with verdicts. Assert run terminal status is `completed`, not `skipped`.

5. **`test_iterate_projects_does_not_set_skipped_when_any_real_failure`** — one project skipped, one raises in `orchestrate_project`. Assert run terminal status is `failed`, not `skipped`.

6. **`test_run_lifecycle_maps_skipped_status`** — drive `handle_finished` (or equivalent) with agent-side `status="skipped"`. Assert row `status == "skipped"`.

7. **`test_run_lifecycle_does_not_remap_skipped_to_failed`** — regression pin against future map drift.

### Agent (`agent/tests/` or `dashboard/backend/tests/` as repo convention dictates)

8. **`test_orchestrate_project_returns_work_attempted_true_when_lead_spawned`** — mock the SDK session to spawn at least one teammate. Assert third tuple element is `True`.

9. **`test_orchestrate_project_returns_work_attempted_false_when_no_eligible_issues`** — mock the SDK session to report no eligible issues. Assert third tuple element is `False`.

### Frontend

10. Type-check / unit test confirming `"skipped"` is a valid status value and `"project_skipped_no_work"` is a valid event name. Visual rendering verified manually post-deploy (badge color, distinct from `failed`).

### End-to-end / smoke

11. Trigger a run with no eligible issues in any configured project. Confirm via API:
    - `runs.status == "skipped"` (not `failed`).
    - `runs.skip_reason` preserved.
    - Dashboard webhook log shows `project_skipped_no_work` event, NOT `manager_no_verdicts`.
    - Digest entry for the project shows `SKIP`, not `ERROR`.

## Backwards Compatibility

- Historical rows with `status="failed" + skip_reason="no-eligible-issues-proposals-pending"` are unchanged. They remain `failed` in the DB. No migration.
- `manager_no_verdicts` continues to fire for the real-failure case; existing consumers see no change in that pathway.
- The new `skipped` status is purely additive at the status enum; consumers that don't know about it will fall through to default rendering (likely indistinguishable from any other unknown status until they update — acceptable since this is an internal app).

## Out-of-Scope Follow-Ups

- DB migration of historical `failed + skip_reason=<idle reason>` rows — possible future cleanup.
- Refactoring the verdicts-file check into an explicit per-project state machine — possible future hygiene.
- Renaming `manager_no_verdicts` to `manager_failed_no_verdicts` — would be cleaner but forces every consumer to update at once.

## Acceptance

- [ ] `orchestrate_project` returns 3-tuple with `work_attempted` bool.
- [ ] `iterate_projects` emits `project_skipped_no_work` (not `manager_no_verdicts`) for the idle case.
- [ ] Run-level terminal status is `skipped` iff all projects skipped AND no real failures.
- [ ] `run_lifecycle.py` maps agent-side `skipped` → row `skipped`.
- [ ] Frontend renders `skipped` distinctly from `failed`.
- [ ] Tests 1–10 above pass; smoke test 11 confirmed against a real run.
- [ ] `docs/architecture.md` updated.
- [ ] PR targets `dev`. Closes #446, Closes #447.
