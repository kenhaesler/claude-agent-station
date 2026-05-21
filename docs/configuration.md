# Configuration

*Reference for every configurable setting. For operators tuning the system.*

## Where config lives

The canonical configuration store is the `config` table in `station.db` (key/value, JSON-encoded values). The dashboard writes here directly. A JSON view of the same config is materialized at `STATION_CONFIG_PATH` for the agent process to read; the dashboard's `config_sync` service keeps the two in sync. **Always edit through the dashboard or the `/api/config` endpoint** — direct edits to the JSON file are overwritten on the next sync.

## Environment variables

Every variable below is prefixed with `STATION_`. They can also be placed in a `.env` file at the project root — see `.env.example` for a copyable template covering every var.

| Variable | Default | Description |
|----------|---------|-------------|
| `STATION_DB_PATH` | `/var/lib/claude-agent-station/station.db` | Path to the SQLite database file. |
| `STATION_LOG_DIR` | `/var/log/claude-agent` | Directory where agent run logs are written. |
| `STATION_CONFIG_PATH` | `/home/claude-agent/.claude/autonomous/manager-config.json` | Path of the JSON config file read by the agent process. Kept in sync with `station.db` by the dashboard. |
| `STATION_WORKSPACES_DIR` | `/home/claude-agent/workspaces` | Base directory where per-issue git worktrees are created. |
| `STATION_CREDENTIALS_PATH` | `/home/claude-agent/.claude/.credentials.json` | Path to the Claude CLI credentials file used by agent workers. |
| `STATION_HOST` | `127.0.0.1` | IP address the dashboard binds to. |
| `STATION_PORT` | `8420` | TCP port the dashboard listens on. |
| `STATION_WS_POLL_INTERVAL` | `0.5` | WebSocket polling interval in seconds. |
| `STATION_API_KEY` | _(none)_ | When set, all `/api/*` requests (except health and webhook routes) require a matching Bearer token (`Authorization: Bearer <key>`) or `?token=` query parameter. When unset, the API is open — only suitable for a fully isolated host. |
| `STATION_WEBHOOK_SECRET` | _(none)_ | Shared secret for authenticating webhook requests from the agent. When set, all `POST /api/webhook/*` requests must include a matching `X-Webhook-Token` header. When unset, no auth is required. |
| `STATION_GITHUB_WEBHOOK_SECRET` | _(none)_ | Secret for verifying GitHub webhook HMAC-SHA256 signatures. |
| `STATION_ALLOWED_ORIGINS` | `["http://localhost:5173", "http://localhost:4173", "http://127.0.0.1:5173", "http://127.0.0.1:4173"]` | CORS allowed origins. Override with a JSON list or comma-separated string. Extend this when the frontend is served from a different origin than the API. |
| `STATION_LAUNCHER_TOKEN` | _(none — required for compose)_ | Shared secret authenticating dashboard → agent-launcher calls. **`compose.yml` fails fast if unset.** Generate with `openssl rand -hex 32` and put it in `.env` at the repo root. Bare-metal (systemd) deployments only need this when the dashboard and agent run on different hosts. |
| `STATION_AGENT_LAUNCHER_URL` | _(none — required for compose)_ | Base URL of the agent launcher HTTP service (e.g. `http://agent:8421`). Used by the dashboard's `service_control` module to start/stop runs and query launcher status in compose mode. Must include scheme and port; no trailing slash needed. |
| `STATION_LAUNCHER_ZOMBIE_TIMEOUT_S` | `120` | Seconds of webhook silence after which the launcher's proactive zombie-reaper declares an alive subprocess stale and sends SIGTERM. Set generously to avoid false positives during legitimate quiet stretches (e.g. `gh issue list` on a slow network). See #360. |
| ~~`STATION_LAUNCHER_USE_BASH`~~ | _(removed)_ | Removed in #383. The launcher always spawns `python3 -m agent.station_orchestrator --driver`. `agent/scripts/run-manager.sh` was deleted in the same PR. |
| `STATION_RUN_STALE_THRESHOLD_S` | `60` | Seconds of webhook silence after which the dashboard's reactive recovery path (triggered on a user-initiated `/api/runs/trigger` that gets a 409) considers the orchestrator's run row stale enough to justify a force-stop. Half the launcher's reaper threshold because reactive recovery fires only on explicit user retries. |
| `STATION_PROVIDER_KEYS_PATH` | `~/.claude-agent-station/provider_keys.json` | Path to the bring-your-own-key store for third-party LLM providers (OpenAI Codex, Google Gemini). Compose mounts this on `station-data` so saved keys survive container rebuilds. The file is chmod 0600 from creation; raw keys are never returned over the API. |
| `STATION_VISION_UPLOAD_DIR` | `/var/lib/claude-agent-station/vision-chat-uploads` | Directory where vision chat attachments live before commit. The directory must be writable by the backend process; entries are cleaned up on session approve/cancel and by the periodic vision-cleanup sweep. |

## Launcher entry point (#361, updated #383)

The agent launcher's `/run` endpoint spawns the orchestrator as a detached subprocess. Since #383 the entry point is exclusively the Python driver:

| Command spawned |
|-----------------|
| `python3 -m agent.station_orchestrator --driver --run-id <id> --config <path> --workspaces-dir <path>` |

`RunDriver` owns the `run_start` / `run_complete` lifecycle via Python `try/finally`. `run_start` ships `project_count` / `max_concurrent` / `concurrent_group_id` / `log_file`, and `run_complete` ships `status` / `exit_code` / `tokens_input` / `tokens_output` / `tokens_total` / `turns` / `duration_ms`. Token/turn counters are copied in-process from the orchestrator's `_StreamState` — no bash telemetry JSON dump is involved.

Each former bash phase now lives in a dedicated Python module under `agent/`:

| Former bash phase | Python module |
|---|---|
| `preflight` | `agent/preflight.py` |
| `setup_workspace` | `agent/workspace_setup.py` |
| `queue_complete_item` / `queue_recover` | `agent/queue_recovery.py` |
| `check_rate_limit` / `record_session` | `agent/rate_limit.py` |
| `run_manager_review` | `agent/manager_review.py` |
| `merge_to_dev` | `agent/integration_branch.py` |
| `write_digest` | `agent/digest.py` |

`agent/scripts/run-manager.sh` was deleted in #383.

Signal handling:

- **SIGINT** (Ctrl-C, `docker compose kill --signal SIGINT`) raises `KeyboardInterrupt` via Python's default handler. The driver catches it and emits `run_complete` with `status="interrupted"`, exit code 130.
- **SIGTERM** (launcher `/stop`, `_zombie_reaper`) is mapped to `KeyboardInterrupt` by a process-level signal handler the driver installs at startup, so it flows through the same interrupted path.

## Models

Defaults per role (from `agent/config/default-config.json`):

| Role | Default | Config key |
|------|---------|------------|
| Employee (Teammate) | `claude-opus-4-7` | `models.employee` |
| Manager (Lead + Manager review) | `claude-sonnet-4-6` | `models.manager` |
| Analyst | `claude-sonnet-4-6` | `models.analyst` |
| Planner | `claude-sonnet-4-6` | `models.planner` |
| Router | `claude-haiku-4-5-20251001` | `models.router` |
| Vision analyst | `claude-sonnet-4-6` | env: `STATION_VISION_ANALYST_MODEL` |

To change a model, set the corresponding key via the dashboard Config page or `PATCH /api/config`. The orchestrator picks up the change on the next run.

Fallback behavior on primary-model API errors is described in [`concepts.md`](concepts.md#plan-usage-throttling) — there is no separate config key.

## Budgets and rate limits

Token and turn budgets are configured under `limits.*` in `station.db` (and reflected in `default-config.json`). Defaults:

| Key | Default | Description |
|-----|---------|-------------|
| `limits.max_usage_percent` | `80` | Weekly plan-usage percentage at which new runs are throttled. |
| `limits.reserve_percent` | `20` | Percentage of plan budget held in reserve. |
| `limits.max_employee_turns` | `200` | Maximum agent turns per Employee (Teammate) run. |
| `limits.max_analyst_turns` | `50` | Maximum turns for Analyst role. |
| `limits.max_planner_turns` | `50` | Maximum turns for Planner role. |
| `limits.max_manager_turns` | `30` | Maximum turns for Manager review. |
| `limits.max_fix_turns` | `75` | Maximum turns for fix-mode runs. |
| `limits.max_triage_turns` | `30` | Maximum turns for triage-mode runs. |
| `limits.max_review_turns` | `30` | Maximum turns for review-mode runs. |
| `limits.max_rejection_retries` | `1` | How many times an employee may retry after a REJECT verdict. |
| `limits.max_concurrent_employees` | `1` | Maximum employees running in parallel across all projects. |
| `limits.max_employees_per_project` | `1` | Maximum employees running in parallel for a single project. |
| `limits.token_budget_strategy` | `equal_split` | How the total token budget is divided among concurrent employees. |

When weekly plan usage crosses `max_usage_percent`, the orchestrator short-circuits new runs entirely — it does not silently downgrade models. See [`concepts.md`](concepts.md#plan-usage-throttling) for the throttle model.

Per-project dollar caps (`max_budget_usd`) are set on individual projects rather than globally — see the [Project config](#project-config) section.

## Schedule

The agent runs on a systemd timer (`agent/systemd/claude-agent.timer`). Default cadence: **hourly** (`OnCalendar=*-*-* *:00:00`).

A separate validation timer (`claude-agent-validate.timer`) runs daily at 06:00 (`OnCalendar=*-*-* 06:00:00`).

To change the cadence, edit the relevant unit file under `agent/systemd/` and reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart claude-agent.timer
```

## Autonomy levels

Per-project setting that gates **how freely** the agent can act on its work. Independent of the [project mode](#project-mode) — applies to every tool call regardless of whether the run is implementing, planning, or analyzing. See [`adr/0001-autonomy-levels.md`](adr/0001-autonomy-levels.md) for the policy engine; this section just summarises what each level does.

| Level | Edits (`Edit` / `Write`) | Destructive bash (`rm -rf`, force push, …) | Always-deny list (push to `main`, fork bombs, `sudo`, …) |
|---|---|---|---|
| `manual` | defer to operator | defer to operator | block |
| `assisted` *(default)* | allow | defer to operator | block |
| `auto` | allow | allow | block |

Every decision — allow, defer, or block — is recorded to `agent_events` with `event_type='auto_mode_decision'` and surfaced on the Audit tab in Settings (`/settings/audit`). The always-deny list is hard-coded in `agent/auto_mode.py` and cannot be overridden, even at `auto`.

## API key and webhook secret

Authentication settings are listed in the env-vars table above. Additional behavior:

- `STATION_API_KEY` is sent as `Authorization: Bearer <key>`, or as `?token=<key>` for SSE clients that cannot set custom headers.
- `STATION_WEBHOOK_SECRET` is sent as the `X-Webhook-Token` header on `POST /api/webhook/*`.
- `STATION_GITHUB_WEBHOOK_SECRET` verifies HMAC-SHA256 signatures on incoming GitHub webhooks.

Exempt from `STATION_API_KEY` auth (verified in `dashboard/backend/app/main.py`): the health router, the internal agent webhook router, the WebSocket log-streaming router (`logs.ws_router` at `/api/logs/ws`, which has its own inline WebSocket auth), the GitHub webhook router, and GitHub App lifecycle endpoints.

### Provider auth (Settings → Auth)

The Auth tab in Settings shows three provider panels:

- **Claude API** — OAuth flow via `/api/oauth/*`; tokens persist to `STATION_CREDENTIALS_PATH` and are auto-refreshed by the dashboard.
- **OpenAI Codex** — bring-your-own-key. Paste an `sk-…` API key; used by the OpenAI Codex teammate role.
- **Google Gemini** — bring-your-own-key. Paste an `AIza…` API key; used by the Gemini analyst role.

The OpenAI and Gemini keys are stored as JSON at `STATION_PROVIDER_KEYS_PATH` (chmod 0600), routed through `/api/provider-keys` (`GET` returns masked status, `PUT /{provider}` saves a key, `DELETE /{provider}` clears it). Raw keys are never returned in responses — only a redacted form like `sk-pro…aBc1`.

### Dispatch telemetry

`GET /api/runs/telemetry-summary` (added by the Pro Dispatch redesign) is the single endpoint backing the four telemetry cells on the home page (Active / Queue / Tokens·7D / System) and the global LiveTicker KPI bar. It aggregates running runs + their teammates, queue counts grouped by lifecycle state, a 7-day token total with daily sparkline points, a 7-day verdict count (APPROVE/PR/REJECT bucketed as `ok`/`pr`/`x`) for the LiveTicker, and a coarse system-health label (`NOMINAL`/`DEGR`/`CRIT`) derived from disk and memory pressure. Lives in `dashboard/backend/app/routers/runs.py`; the response shape is `TelemetrySummaryOut` in `app/schemas.py`.

**Response shape** (see `TelemetrySummaryOut` and the four sub-models in `app/schemas.py` for the authoritative fields and types):

| Sub-object | Fields | What each means |
|---|---|---|
| `active` (`TelemetryActive`) | `count`, `teammates`, `roles[]` | Number of runs in `running` / `plan_reviewing` / `reviewing`, total teammate slots across those runs (from `Run.team_members` JSON, falling back to running `CoordinatorTask` rows), and the distinct role tags found (`backend`, `frontend`, `qa`, `lead`). |
| `queue` (`TelemetryQueue`) | `total`, `claimed`, `done`, `pending`, `other` | Counts of `QueueItem` rows grouped by lifecycle state. `claimed` aggregates `claimed`/`assigned`/`planning`/`in_progress`; `done` aggregates `completed`/`approved`; `pending` is just `pending`; `other` catches anything else (`failed`/`paused`/`cancelled`/...) so the four cells always sum to `total`. |
| `tokens_7d` (`TelemetryTokens7d`) | `total`, `runs`, `input`, `output`, `spark[]` | Sum of `Run.tokens_total` / `tokens_input` / `tokens_output` and run count for the past 7 days. `spark` is always a length-7 array of per-day token totals (oldest → today, missing days backfilled to 0) feeding the cell sparkline. |
| `system` (`TelemetrySystem`) | `status`, `disk_free_gb`, `memory_used_pct`, `uptime_secs` | Coarse health label derived from disk and memory pressure: `NOMINAL` is the default; `DEGR` triggers under 5G free or >70% memory used; `CRIT` triggers under 1G free or >90% memory used. Underlying numbers come from `app.services.systemd.get_system_resources`. |
| `verdicts_7d` (`TelemetryVerdicts7d`) | `ok`, `pr`, `x` | 7-day verdict counts grouped by `Run.verdict` (case-insensitive): `APPROVE` → `ok`, `PR` → `pr`, anything else non-null (`REJECT` and any future terminal verdict) → `x`. Runs with NULL verdicts (still in flight or never reviewed) are excluded. Feeds the `VERDICTS·7D` cell in the LiveTicker. |

## Project config

Each managed repository is one row in the `projects` table. The dashboard's Projects page is the easiest way to edit; the underlying schema (used by `POST /api/projects`) is:

### Two orthogonal axes

Projects carry two independent settings that are sometimes confused. They control different concerns and any combination is legal:

| Axis | Values | What it controls | UI section |
|------|--------|------------------|------------|
| `mode` | `full`, `analyze`, `plan`, `plan_only` | **What** the agent is asked to do — implement, investigate, plan, or plan-then-pause. Shapes the teammate spawn prompt and the manager review criteria. | "Work scope" |
| `autonomy_level` | `manual`, `assisted`, `auto` | **How freely** it can act — whether `Edit`/`Write` and destructive bash defer to the operator or run unattended. Applies to every tool call regardless of mode. | "Execution policy" |

Examples: `analyze + auto` runs read-only investigation without any approval prompts; `full + manual` lets the agent implement but every file write needs operator confirmation; `plan_only + assisted` (the typical onboarding default) writes a plan that the manager and operator review before any code is written.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `repo` | string | _(required)_ | GitHub repository in `owner/repo` format. |
| `priority` | string | `"medium"` | Scheduling priority: `high`, `medium`, or `low`. |
| `mode` | string | `"full"` | Project mode — see [Project mode](#project-mode) below. |
| `enabled` | boolean | `true` | Whether the project is picked up by the scheduler. |
| `branch` | string | `"main"` | Default branch the agent targets (the project trunk). |
| `promotion_target` | string\|null | _(none)_ | Branch the integration meta-PR opens against. When unset, falls back to `branch`. See [Integration branch](#integration-branch). |
| `custom_instructions` | string | _(none)_ | Extra instructions appended to the agent prompt for this project. |
| `setup_script` | string | _(none)_ | Single command run before each agent run (e.g. `npm install`). Must not contain shell metacharacters and is capped at 1024 characters; for richer setup, commit a script to the repo and reference its path here. |
| `security_review_enabled` | boolean | `false` | Whether a security review pass is added to the agent's workflow. |
| `autonomy_level` | string | `"assisted"` | Per-project autonomy level. See [`adr/0001-autonomy-levels.md`](adr/0001-autonomy-levels.md). |
| `max_budget_usd` | float | _(none)_ | Optional per-project dollar cap per run. When unset, no project-level cap is enforced. |

Example JSON for the `POST /api/projects` request body:

```json
{
  "repo": "acme-org/backend-api",
  "priority": "high",
  "mode": "full",
  "enabled": true,
  "branch": "main",
  "autonomy_level": "assisted",
  "setup_script": "pip install -r requirements.txt"
}
```

### Project mode

Each project picks one of four modes. The orchestrator branches on this
value to shape both the teammate spawn prompt and the manager review
package (issue #266). Out-of-scope values (`triage`, `review`, `fix`)
exist on the schema for legacy reasons but are coerced to `full` by the
Agent Teams orchestrator.

| Mode | Spawn-prompt block | Worker behavior | Manager review |
|------|--------------------|-----------------|----------------|
| `full` | _(none)_ | Plan + implement + push branch. | Full Mode Review (default). |
| `analyze` | `ANALYZE_MODE` | Read-only investigation; writes findings to `.claude-analyze-report-{index}.json`; never modifies source, never branches, never commits. | `MODE: ANALYZE` header → Analyze Mode Review. Never rejects for "no code changes". |
| `plan` | _(none)_ | Plan-quality output; source must be untouched. | `MODE: PLAN` header → Plan Mode Review. Rejects if any source file was modified. |
| `plan_only` | `PLAN_ONLY_MODE` | Writes a plan to `.claude-employee-plan-{index}.json` and stops; no branch, no commit, no push. | `MODE: PLAN_REVIEW` header → Plan Review Mode. Verdicts: `APPROVE_PLAN` / `REVISE_PLAN` / `REJECT_PLAN`. |

#### Plan-review gate (`plan_only` only)

`plan_only` introduces a pre-implementation gate between plan-writing and
code. The gate is implemented by `agent/plan_review_gate.py` and invoked
by `agent/project_loop.py::iterate_projects` after the manager review phase.

**Pipeline (per `plan_only` project):**

1. `iterate_projects` emits `plan_review_start` (→ `Run.status = plan_reviewing`) before invoking the manager review.
2. Manager review runs and writes verdicts to `run-<id>-verdicts.json`.
3. `iterate_projects` invokes `python -m agent.plan_review_gate` for each plan_only project. The driver:
   - POSTs `awaiting_plan_review` (→ `Run.status = awaiting_plan_review`) to mark the gate as engaged.
   - Parses each plan verdict and dispatches per the table below.
   - POSTs a terminal status event (`plan_approved` / `plan_rejected`) once all verdicts are processed.

**Verdict actions:**

| Verdict | Side effects | Run.status (terminal) |
|---------|-------------|------------------------|
| `APPROVE_PLAN` | POSTs a new `QueueItem` to `/api/queue` with `mode=full`, `state=pending`, and `context={"approved_plan_path": ..., "from_plan_only_run": true}`. The follow-up `full` run picks this up on the next cycle and the implementing teammate reads the approved plan as `APPROVED_PLAN` guidance. | `plan_approved` |
| `REVISE_PLAN` (within budget) | Writes the manager feedback to `<workspace>/.claude-plan-revision-feedback-<index>.json` for the next teammate spawn to consume. **The live re-spawn loop is a documented TODO** — feedback is durably persisted but the orchestrator does not yet re-inject it into a running SDK session. Expect a manual trigger or follow-up issue. | stays at `awaiting_plan_review` |
| `REVISE_PLAN` (past budget) | Treated as a soft reject. | `plan_rejected` |
| `REJECT_PLAN` | Logged. No queue POST, no follow-up run. | `plan_rejected` |

If the queue POST for `APPROVE_PLAN` fails (network error, dashboard
down, dedup hit), the run is **not** flipped to `plan_approved` —
it stays in `awaiting_plan_review` so an operator can re-run the gate
manually. The gate is best-effort by design.

**Run statuses added** (additive — old code keeps working):
`awaiting_plan_review`, `plan_approved`, `plan_rejected`. The dashboard
surfaces these via a banner on Mission Control and the Agent Teams
canvas.

| Env var | Default | Description |
|---------|---------|-------------|
| `STATION_PLAN_REVISION_MAX` | `2` | Maximum `REVISE_PLAN` iterations before the gate auto-rejects. Read at gate-evaluation time so changes apply on the next gate run. |
| `STATION_DASHBOARD_URL` | `http://127.0.0.1:8420` | Dashboard base URL the gate POSTs to. Falls back to `STATION_WEBHOOK_URL` (with `/api/webhook/...` stripped) and finally the default. |
| `STATION_API_KEY` | _(unset)_ | When set, the gate adds `Authorization: Bearer <key>` on `/api/queue` POSTs. |
| `STATION_WEBHOOK_SECRET` | _(unset)_ | When set, the gate adds `X-Webhook-Token: <secret>` on `/api/webhook/run-event` POSTs. |

### Vision-driven issue bootstrap

When a project has `docs/vision.md`, two automatic triggers fire the vision analyst:

- **Trigger A (orchestrator):** triggered runs that find no eligible issues dispatch the analyst when no `vision-suggested` issues are already open. The triggering run terminates with `Run.skip_reason = no-eligible-issues-bootstrap-dispatched`.
- **Trigger B (vision commit):** committing a new vision via the dashboard fires the analyst when the document SHA changes. Idempotent on identical re-commits via `Project.last_vision_analyzed_sha`.

Both produce `Run.mode = vision-bootstrap` rows that surface in the Runs list and Mission Control. Issues land with the `vision-suggested` label; remove the label to accept (the orchestrator's `SKIP_LABELS` blocks autonomous implementation until then).

### Vision reference files

Users may attach reference files (PDFs, images, xlsx, csv, docx, txt, md — up to 10 MB each, 40 MB total per session) to the vision chat. Uploads are staged on disk under `STATION_VISION_UPLOAD_DIR/<session_id>/` and tracked in the database.

**New database table:**

- `vision_chat_attachments` — reference files attached to a vision chat. One row per upload, scoped to a `vision_chat_sessions.id` (cascade delete).

**Python dependencies** (in `dashboard/backend/requirements.txt`):

- `openpyxl`, `python-docx` — text extraction for non-native attachment types.
- `python-magic` — MIME sniffing (requires system `libmagic`; on Rocky/RHEL install with `dnf install file-libs file-devel`).

On **Approve & commit**, reference files that were included in at least one chat turn are written to `docs/vision-refs/` in the target repo and listed in a `## References` section of `docs/vision.md`. Teammates pick them up automatically via `git clone`.

## Integration branch

When `integration.enabled` is true, each project gets a long-lived integration branch
(`integration.dev_branch`, default `autonomous/dev`). Agent work that earns an
`APPROVE` verdict is merged into that branch instead of being PR'd to the trunk one
feature at a time. The integration branch is later promoted into the project's
`promotion_target` (or `branch`, if no target is set) via a single meta-PR opened by
`agent/scripts/promote.sh`.

### Top-level config (`manager-config.json`)

These keys live under `integration.*` and apply to every project:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `false` | Turns on the integration-branch flow. |
| `dev_branch` | string | `"autonomous/dev"` | Branch name created in each target repo (e.g. `claude-agent-station`). The orchestrator pushes this branch to `origin` on first use so `gh pr create --base <dev_branch>` resolves; a push failure (branch protection / perms) is logged at WARNING and subsequent verdict execution surfaces the resulting PR-creation failure as ERROR instead of silently recording APPROVE. |
| `promotion_strategy` | string | `"batch"` | `batch` opens one meta-PR with N commits; `individual` opens one PR per cherry-picked commit. |
| `auto_validate` | boolean | `true` | Run the project's test suite on the integration branch after each merge (`agent/scripts/integration-branch.sh::validate_dev`). |
| `auto_promote` | boolean | `false` | When validation passes, open the meta-PR automatically. |
| `auto_bisect` | boolean | `true` | If validation fails, revert the last merge on the integration branch to identify the culprit. |

These can also be edited in **Settings → Integration**, which writes through the same
`PUT /api/config` endpoint.

### Per-project `promotion_target`

`promotion_target` is the PR base used when the integration branch is promoted. When
unset, the meta-PR opens against the project's `branch` field. Typical setup:

- `branch: "main"` — the project trunk (also used as the initial base when first
  creating the integration branch and as the conceptual source-of-truth).
- `promotion_target: "dev"` — where the integration meta-PR opens. Keeps `main`
  protected and lets the user promote `dev → main` separately.

`promote.sh` also rebases the integration branch onto `promotion_target` before
opening the meta-PR, so the PR diff contains only the agent's commits — not unrelated
trunk drift.

Edit per-project in **Projects → Settings → Promotion target**.

## Conflict resolution

When the agent's auto-created PRs hit merge conflicts, a layered resolver
(mechanical → lockfile → LLM) runs to attempt resolution within a rolling
24-hour token budget per branch. See spec at
`docs/superpowers/specs/2026-05-10-conflict-resolution-design.md`.

### Top-level config (`manager-config.json`)

| Key | Default | Notes |
|---|---|---|
| `conflict_resolution.enabled` | `true` | master switch |
| `conflict_resolution.rolling_24h_token_budget` | `200000` | tokens (input + output combined) per head branch over a sliding 24h window |
| `conflict_resolution.max_feedback_rounds` | `3` | shared counter across test failures and manager REJECTs |
| `conflict_resolution.model` | `"claude-opus-4-7"` | overridable; SDK fallback chain still applies |
| `conflict_resolution.max_turns` | `30` | per resolver invocation |
| `conflict_resolution.lock_ttl_seconds` | `1800` | flock TTL for `/var/lib/claude-agent-station/locks/conflict-<branch>.lock` |
| `conflict_resolution.force_push_with_lease` | `true` | unconditional v1; reserved for future opt-out |

### Per-project override

To disable LLM resolution for a specific repo (mechanical+lockfile only):

```json
{
  "projects": [
    {
      "repo": "acme/sensitive",
      "conflict_resolution": {
        "rolling_24h_token_budget": 0
      }
    }
  ]
}
```

### Per-project test command

To run the project's tests as part of post-resolution validation, set
`test_command` on the project entry. If absent, the manager review is
the only validation gate.

```json
{
  "projects": [
    {
      "repo": "laboef1900/next-itsm",
      "test_command": "npm test --silent"
    }
  ]
}
```

## Verdict tiers

The manager produces one of four verdicts per project/issue:

| Verdict | Action | When |
|---|---|---|
| `APPROVE` | Direct merge to base branch (or to integration's dev branch when enabled) | Tests pass, scope is normal, no sensitive code touched. |
| `APPROVE_INTEGRATION` | Non-draft PR against the integration/dev branch with `gh pr merge --auto --squash` armed | Work is complete and tested but touches sensitive code (auth, payments, config), or scope is large enough to want CI as the gate before landing. CI passes → PR auto-merges with no human click. |
| `PR` | Draft PR for human review | Ambiguous requirements, tests skipped, or scope > 30 files. A human must look. |
| `REJECT` / `SKIP` | No merge | Work incomplete (`REJECT`) or no eligible work (`SKIP`). |

### Prerequisite for `APPROVE_INTEGRATION`

`APPROVE_INTEGRATION` arms GitHub's auto-merge feature. Auto-merge only meaningfully gates when the integration/dev branch has **at least one required check** in its branch protection rules. If no checks are required, `gh pr merge --auto --squash` will merge immediately. Configure required checks at `Settings → Branches → Branch protection rules → <dev_branch>` on each project before relying on this verdict.

If the project does not have integration enabled (`integration.enabled = false`), the verdict degrades to `APPROVE` and a warning is logged — the manager should not have emitted `APPROVE_INTEGRATION` in that case, but the system accepts rather than failing the run.

## SQLite → Postgres migration playbook

The persistence layer supports both SQLite (single-writer, file-based) and Postgres (multi-writer, recommended once concurrent runs land in #386). Migration is a one-time data copy.

### Backend selection

Set the SQLAlchemy URL via env var. URLs are read at process start:

- **SQLite (default)**: `STATION_DB_PATH=/var/lib/claude-agent-station/station.db` — synthesized as `sqlite+aiosqlite:///<path>`.
- **Postgres**: `STATION_DB_URL=postgresql+asyncpg://station:<password>@db:5432/station` — explicit driver + creds. The `db` service in `compose.yml` provides this on the compose deployment path.

`STATION_DB_URL` takes precedence over `STATION_DB_PATH`.

### Keeping the password out of process env

To avoid baking the password into the SQLAlchemy URL (and therefore into `/proc/<pid>/environ` and `docker inspect`), set the literal token `${DB_PASSWORD}` in `STATION_DB_URL` and point `STATION_DB_PASSWORD_FILE` at a file containing the password. The dashboard and agent each read the file at startup and substitute the token before constructing the engine. Compose mounts `./.secrets/db_password` into both containers at `/run/secrets/db_password` via the `secrets:` block — operators only need to put their chosen password in `./.secrets/db_password` (which `.gitignore` excludes) before `docker compose up`.

If `STATION_DB_PASSWORD_FILE` is unset or unreadable, the placeholder is preserved and the engine fails with a clear auth error rather than connecting with an empty password.

### One-time data migration

1. **Stop the agent and dashboard.** Both must be quiesced — the migrator does a row-count parity check at the end; concurrent writes during the copy would invalidate it.
   ```bash
   systemctl stop claude-agent claude-dashboard   # systemd
   docker compose stop agent dashboard            # compose
   ```
2. **Backup the SQLite file.**
   ```bash
   cp /var/lib/claude-agent-station/station.db station.db.pre-pg-$(date +%Y%m%dT%H%M%S).bak
   ```
3. **Start the Postgres service** and apply the schema:
   ```bash
   docker compose up -d db                              # compose path
   cd dashboard/backend
   STATION_DB_URL=postgresql+asyncpg://station:<pw>@localhost:5432/station \
       python -m alembic upgrade head
   ```
4. **Run the migrator** from the repo root:
   ```bash
   python -m scripts.migrate_sqlite_to_postgres \
       --sqlite /var/lib/claude-agent-station/station.db \
       --postgres "postgresql+asyncpg://station:<pw>@localhost:5432/station"
   ```
   The script copies rows table-by-table in dependency order, JSON-decodes the four JSONB columns (`agent_events.event_data`, `audit_log.action_detail`, `runs.employee_report`, `runs.verdict_detail`) on the way in, advances SERIAL sequences to `max(id)`, and prints a row-count parity table. Exit code is non-zero on any mismatch.
5. **Switch the backend** for the agent + dashboard services:
   ```bash
   # systemd
   echo 'STATION_DB_URL=postgresql+asyncpg://station:<pw>@db:5432/station' \
       >> /etc/claude-agent-station/env
   systemctl restart claude-agent claude-dashboard

   # compose: edit compose.yml to set STATION_DB_URL on agent + dashboard
   docker compose up -d
   ```
6. **Verify**: open Mission Control; the historical runs list should show identical counts. Trigger a small test run and confirm `last_event_at` updates in real time (LISTEN/NOTIFY path).

### Rollback

The original SQLite file is untouched. If Postgres misbehaves:
1. `unset STATION_DB_URL` (or remove from systemd env / compose).
2. Restart agent + dashboard.
3. SQLite resumes as the canonical store — any rows written to Postgres after the cutover are lost; re-export from Postgres back to SQLite is not currently scripted.

### Idempotency

The migrator uses `INSERT ... ON CONFLICT DO NOTHING` on every table, so re-running against a partially-populated Postgres is safe — already-migrated rows are skipped, new rows fill in. The row-count parity check at the end still requires source == destination, so a partial copy will fail loudly.

### Operational implications

- **Polling intervals relax on Postgres** (issue #393 PR-3): `log_importer` poll bumps from 30 s to 300 s (`run_event` LISTEN/NOTIFY carries the recency load); `stale_run_reaper` tick bumps from 15 s to 60 s (`heartbeat` LISTEN/NOTIFY rebroadcasts on the SSE event bus). No operator action — these are dialect-aware automatically.
- **Concurrent runners** (issue #386 follow-up) require Postgres because SQLite's single-writer lock would serialise their event writes. Migration is the prerequisite for that work.

### Issue splitter (#391)

| Env var | Default | Notes |
|---|---|---|
| `STATION_SPLIT_ENABLED` | `0` | Set to `1` to enable the issue-splitter pre-dispatch hook. Off by default during rollout. |

| Label | Purpose |
|---|---|
| `splitter-proposed` | Sub-issue created by the splitter; operator must remove the label before autonomous pickup. Mirrors `vision-suggested`. |
| `split-me` | Operator opt-in: always split this issue, even if heuristics say no. |
| `do-not-split` | Operator opt-out: never split this issue, even if heuristics say yes. Veto wins over everything. |
| `split` | Added automatically to a parent after its sub-issues are created so the router doesn't re-consider it. |
| `splitter-needs-rework` | Added to the parent when all sub-runs fail. |
