# Vision-driven issue bootstrap

*Design spec — 2026-05-08*

## Goal

A project's vision becomes its initial backlog without manual steps. Two triggers feed the existing `vision_analyst` worker:

- **Trigger A** — orchestrator fires the analyst when a triggered run finds no eligible issues but the project has a vision.
- **Trigger B** — backend fires the analyst when a vision commit changes the document SHA.

Both surface as a distinct **`vision-bootstrap`** run type in the dashboard, so operators can tell "creating issues" apart from "implementing issues" at a glance.

## Non-goals

- Auto-accepting proposals. The existing `SKIP_LABELS` gate (`vision-suggested`) stays — humans accept by removing the label.
- Scheduled / cron-style vision sweeps. Future enhancement.
- Multi-project parallel analysis. The single-instance launcher lock is sufficient for now.
- Rendering proposals inside the dashboard. They live on GitHub; the UI deep-links there.

## What already exists (do not rebuild)

| Layer | File | What it does |
|---|---|---|
| Worker | `agent/vision_analyst.py` | Reads `docs/vision.md`, snapshots repo state, calls a model, creates issues with the `vision-suggested` label. Capped at `MAX_PROPOSALS = 5`. |
| Launcher | `agent/launcher.py:176-212` | `POST /vision-analyst?project_id=N` spawns the worker detached. Single-instance lock at line 190 returns 409 if already running. |
| Backend dispatch | `dashboard/backend/app/routers/vision.py:242` (existing) + `app/services/service_control.py:start_vision_analyst` | Routes manual dispatch from the dashboard; works in compose and systemd. |
| Orchestrator skip | `agent/station_orchestrator.py:89` | `SKIP_LABELS` includes `"vision-suggested"`, so analyst-created issues are not auto-implemented. |
| Tests | `dashboard/backend/tests/test_vision_analyst.py`, `test_vision_router.py` | Cover the worker and the existing manual route. |

This design only adds *triggers* and *UI surfacing*. The worker, launcher endpoint, and dispatch service stay as-is.

## Run-type contract

A new value `"vision-bootstrap"` for `Run.mode`, alongside the existing `"agent-teams"`. Run lifecycle and webhook plumbing reuse the existing `/api/webhook/run-event` endpoint.

| Field | Value |
|---|---|
| `Run.mode` | `"vision-bootstrap"` |
| `Run.status` lifecycle | `running` → `completed` (or `failed` / `interrupted`) |
| `Run.verdict` | unused (no manager review for these runs) |
| Carries on completion | `vision_bootstrap_count`, `vision_bootstrap_proposals` (new nullable columns) |
| Lifecycle | Single-shot — never spawns teammates, never opens PRs |
| Visible in UI as | "Vision bootstrap" with a distinct icon (proposed: ✨ / lightbulb) |

`Run.mode` is currently free-form text in the model; no enum constraint to widen. Frontend `lib/types.ts` already exposes `mode` as a string. New mode values are non-disruptive additions.

## Trigger A — orchestrator dispatches on empty backlog

### Decision site

`agent/station_orchestrator.py`, in the per-project loop where today the orchestrator logs `"No eligible issues for {repo}, skipping"`. New flow:

```
for project in projects:
    issues   = fetch_open_issues(project)
    eligible = filter_eligible(issues)              # SKIP_LABELS etc.
    if not eligible:
        if has_vision(workspace) and not has_open_proposals(project):
            dispatch_vision_bootstrap(project)
        else:
            log_skip_reason(project, has_vision, has_open_proposals)
        continue
    ...
```

### Dispatch is fire-and-forget

`dispatch_vision_bootstrap` calls the same launcher endpoint the dashboard already uses (`POST /vision-analyst?project_id=N`). The orchestrator does NOT run the analyst inline. Reasons:

1. Reuses tested infrastructure (process model, log paths, the single-instance lock at `launcher.py:190`).
2. The regular run terminates promptly with a clear status; the bootstrap shows up as its own run record.
3. A duplicate from B is rejected cleanly with 409.

### Skip reasons surface UI hints

The regular run terminates `status: completed` with a new `Run.skip_reason` text field populated:

| `skip_reason` | UI hint |
|---|---|
| `no-eligible-issues-no-vision` | "No vision yet — define one in the Vision tab so the agent can bootstrap." |
| `no-eligible-issues-bootstrap-dispatched` | "Vision analyst dispatched — see run #N." |
| `no-eligible-issues-bootstrap-already-running` | "Vision analyst is already running — see active runs." |
| `no-eligible-issues-proposals-pending` | "N `vision-suggested` issues await your acceptance." |

`skip_reason` is a free-form text field used only for UI hints. Same pattern as the existing `Run.verdict_detail`. No status churn — keeps every existing consumer of `Run.status` working unchanged.

### Run-record creation

The `vision_analyst` worker creates the `vision-bootstrap` Run record itself. On startup it POSTs to `/api/webhook/run-event` with `mode: "vision-bootstrap"` and a fresh `run_id`. On completion it posts back with `status: completed`, `vision_bootstrap_count`, and `vision_bootstrap_proposals`. Existing webhook auth (`STATION_WEBHOOK_SECRET`), the run-lifecycle service, and the SSE event bus are reused unchanged.

## Trigger B — vision commit fires the analyst

### Site

`dashboard/backend/app/routers/vision.py:71` — `POST /api/projects/{project_id}/vision` (`commit_vision`). After the GitHub commit succeeds and the cache update lands, fire the analyst as a side-effect.

### Content-hash gate

A new `Project.last_vision_analyzed_sha` column (nullable text) records the `vision_cached_sha` current at the last analyst dispatch.

```
new_sha = result.commit_sha
if project.last_vision_analyzed_sha == new_sha:
    return  # nothing changed, no fire
service_control.start_vision_analyst(project_id)   # async
project.last_vision_analyzed_sha = new_sha
```

The SHA is set **at dispatch time, not on completion**. Rationale: a failed analyst run shouldn't cause a retry-loop on subsequent same-content commits. If the user actually wants to retry, they hit the manual "Re-run analyst" button on the Vision tab.

### Single-instance contract

`launcher.py:190` returns 409 if `vision-analyst` is already running. The vision-commit hook treats 409 as success — the goal is "an analyst run will happen", not "this specific call did it". 409 is logged at INFO and the router returns 200 to the dashboard.

### No new endpoint

The hook is implementation-internal to `commit_vision`. Tests stub `service_control.start_vision_analyst`.

### Trigger surface summary

| Trigger | Site | Sync? | Fires when |
|---|---|---|---|
| A — orchestrator on empty backlog | `station_orchestrator.py` per-project loop | Async (POST to launcher) | `eligible == 0` AND `has_vision` AND no open `vision-suggested` issues |
| B — vision commit | `routers/vision.py:commit_vision` | Async (POST to launcher) | Vision SHA changed from `last_vision_analyzed_sha` |
| Manual | Existing dispatch route + Vision tab button | Async | Always (existing behavior preserved) |

All three converge on the same launcher endpoint and worker, so behavior, logs, and observability are identical regardless of trigger.

## UI

### Run-type rendering

Wherever `Run.mode` renders (Runs page list, Run detail header, Mission Control header, Agent Teams canvas):

- **Icon + label:** `✨ Vision bootstrap` (final glyph chosen during impl from the existing icon set)
- **Color:** `--color-violet` accent — distinct from agent-teams; vision is product/strategy-flavored
- **Status row, when running:** "Generating issues from vision…"
- **Status row, when completed:** "Created N issues — `vision-suggested`"
- **Status row, when failed:** failure reason + "Re-run" button (calls existing manual dispatch)

### Skip-reason hints on regular runs

The four `skip_reason` values from the trigger-A table render as a one-line secondary hint under the status badge in the Runs list, and in the summary panel of the Run detail page. The text-and-link mapping is in the table above.

### Vision tab additions (`VisionTab.svelte`)

A new info strip above the existing chat/render surface:

```
┌───────────────────────────────────────────────────────────────┐
│ Vision analyst                                                │
│ Last analyzed: 2h ago · 3 proposals open · 1 accepted last week│
│ [Re-run analyst]   [View proposals on GitHub]                 │
└───────────────────────────────────────────────────────────────┘
```

Data sources:

- *Last analyzed* — `Project.last_vision_analyzed_sha` joined with the latest `vision-bootstrap` run for this project (latest `started_at`).
- *Proposals open* — count of open issues with the `vision-suggested` label, served by a new `GET /api/projects/{id}/vision/proposals` (added to `dashboard/backend/app/routers/vision.py`) returning `{open: int, accepted_recent: int}`. Cached for 60 s server-side to limit `gh` calls.
- *Re-run analyst* — existing manual dispatch endpoint.
- *View proposals on GitHub* — deep link `https://github.com/{repo}/issues?q=is:open+label:vision-suggested`.

### Toasts and live signals

- **B fires** (vision commit) → toast: "Vision analyst running — proposals will appear in a few minutes."
- **`vision-bootstrap` run completes** (any trigger) → toast on the originating tab: "N issues created from vision. [Review on GitHub]"
- **Mission Control / Agent Teams canvas** picks up the live `vision-bootstrap` run via the existing SSE event bus. No new event types needed; the `run_event` payload simply carries `mode: vision-bootstrap`.

### Surfaces that do NOT change

- Project create wizard (vision is added later via the Vision tab).
- Settings page.
- Queue board (vision-bootstrap runs don't queue — they're triggered, not enqueued).

## Edge cases

- **A and B race.** Launcher's `_current_analyst` lock at `launcher.py:190` returns 409 to the second caller. Both A and B treat 409 as "good enough" and log at INFO. No retry storms.
- **Worker model call fails.** `vision-bootstrap` run terminates `status: failed`, error message in `verdict_detail`. UI shows failure + "Re-run" button. No issues created.
- **Worker creates 0 proposals.** Run terminates `status: completed`, `vision_bootstrap_count: 0`. UI: "Vision analyzed — no gaps found." Valid outcome, not an error.
- **`docs/vision.md` exists but parses empty.** `agent/vision.py:load_vision` returns `None` on all-empty sections — orchestrator treats as no vision → `no-eligible-issues-no-vision` skip reason.
- **Duplicate proposal text across runs.** `vision_analyst._gather_repo_state` already includes existing open + closed issues in the prompt context, and the prompt instructs the model not to duplicate. Not airtight, but adequate behind the human-acceptance gate. Out of scope to enforce in code; flag for future hardening if operators report dupes.
- **Open `vision-suggested` issues from a prior run.** A skips dispatch (per the trigger condition). B still fires when the user rewrites the vision; the worker's prompt context will see existing proposals and either dedupe or supersede.
- **`last_vision_analyzed_sha` not yet backfilled.** New column is nullable; `NULL == new_sha` is false, so the first commit after rollout dispatches once. Subsequent same-content commits don't.
- **GitHub PAT/App token missing.** Worker fails at `gh issue create`; run terminates `failed` with a clear message. Same failure mode as today's manual dispatch — no new auth surface introduced.
- **Concurrent `agent-teams` run.** Independent. Vision-bootstrap and teammate runs are orthogonal.

## Tests

| Test | Layer | Asserts |
|---|---|---|
| `test_orchestrator_dispatches_vision_bootstrap_on_empty_backlog` | backend (orchestrator) | `eligible=[]` + has-vision + no-open-proposals → calls `start_vision_analyst`; run terminates `skip_reason="no-eligible-issues-bootstrap-dispatched"` |
| `test_orchestrator_skips_dispatch_when_proposals_pending` | backend | Same condition with one open `vision-suggested` issue → does NOT dispatch; `skip_reason="no-eligible-issues-proposals-pending"` |
| `test_orchestrator_no_vision_skip_reason` | backend | No `docs/vision.md` → does NOT dispatch; `skip_reason="no-eligible-issues-no-vision"` |
| `test_orchestrator_skips_dispatch_when_analyst_running` | backend | Launcher returns 409 → run terminates `skip_reason="no-eligible-issues-bootstrap-already-running"` |
| `test_commit_vision_fires_analyst_on_sha_change` | backend (router) | Mock `start_vision_analyst`; commit twice with different SHAs → called twice; same SHA → called once |
| `test_commit_vision_treats_409_as_success` | backend | Mock launcher returning 409 → router returns 200 |
| `test_vision_bootstrap_run_lifecycle` | backend (run-lifecycle service) | Webhook with `mode=vision-bootstrap`, `vision_bootstrap_count=N` → row written, SSE event emitted |
| `test_runs_router_filters_by_mode` | backend | Existing `?mode=agent-teams` filter still works; new `?mode=vision-bootstrap` returns only those rows |
| `test_proposals_endpoint_returns_open_count` | backend | `GET /api/projects/{id}/vision/proposals` returns the open + accepted-recent counts |
| `VisionTab.test.ts` smoke | frontend (Vitest) | Renders "Last analyzed" and proposal count from a fixture API response; "Re-run" button calls dispatch endpoint |
| Manual playthrough | end-to-end | (a) Trigger run on project with vision + zero issues → vision-bootstrap run appears, issues land on GitHub. (b) Edit vision via dashboard → toast + new run. (c) Edit vision again with no content change → no new run. |

## Migration & rollout

Add four nullable columns via the existing in-code `ALTER TABLE` pattern in `dashboard/backend/app/database.py`:

- `Project.last_vision_analyzed_sha TEXT NULL`
- `Run.skip_reason TEXT NULL`
- `Run.vision_bootstrap_count INT NULL`
- `Run.vision_bootstrap_proposals TEXT NULL` (JSON-encoded list)

No backfill needed. No flag/rollback — new behavior fires only under conditions that previously did nothing (empty backlog, vision commit). Existing flows untouched.

Docs updated per CLAUDE.md project rule:

- `docs/configuration.md` — `STATION_VISION_ANALYST_MODEL` env var (already exists in code; documented here as part of this rollout) plus the trigger conditions.
- `docs/architecture.md` — paragraph on the bootstrap run type and a small diagram update showing the new edge.

## Open items deliberately deferred

- Final glyph for the `vision-bootstrap` run icon — chosen during implementation from the existing icon set.
- The exact wording of the Vision tab info strip — likely refined during UI implementation.
- Idempotency hardening for the worker (server-side dedupe of proposed issue titles) — track separately if dupes appear in practice.
