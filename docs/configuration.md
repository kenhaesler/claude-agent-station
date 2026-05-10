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

### Dispatch telemetry

`GET /api/runs/telemetry-summary` (added by the Pro Dispatch redesign) is the single endpoint backing the four telemetry cells on the home page (Active / Queue / Tokens·7D / System). It aggregates running runs + their teammates, queue counts grouped by lifecycle state, a 7-day token total with daily sparkline points, and a coarse system-health label (`NOMINAL`/`DEGR`/`CRIT`) derived from disk and memory pressure. Lives in `dashboard/backend/app/routers/runs.py`; the response shape is `TelemetrySummaryOut` in `app/schemas.py`.

**Response shape** (see `TelemetrySummaryOut` and the four sub-models in `app/schemas.py` for the authoritative fields and types):

| Sub-object | Fields | What each means |
|---|---|---|
| `active` (`TelemetryActive`) | `count`, `teammates`, `roles[]` | Number of runs in `running` / `plan_reviewing` / `reviewing`, total teammate slots across those runs (from `Run.team_members` JSON, falling back to running `CoordinatorTask` rows), and the distinct role tags found (`backend`, `frontend`, `qa`, `lead`). |
| `queue` (`TelemetryQueue`) | `total`, `claimed`, `done`, `pending`, `other` | Counts of `QueueItem` rows grouped by lifecycle state. `claimed` aggregates `claimed`/`assigned`/`planning`/`in_progress`; `done` aggregates `completed`/`approved`; `pending` is just `pending`; `other` catches anything else (`failed`/`paused`/`cancelled`/...) so the four cells always sum to `total`. |
| `tokens_7d` (`TelemetryTokens7d`) | `total`, `runs`, `input`, `output`, `spark[]` | Sum of `Run.tokens_total` / `tokens_input` / `tokens_output` and run count for the past 7 days. `spark` is always a length-7 array of per-day token totals (oldest → today, missing days backfilled to 0) feeding the cell sparkline. |
| `system` (`TelemetrySystem`) | `status`, `disk_free_gb`, `memory_used_pct`, `uptime_secs` | Coarse health label derived from disk and memory pressure: `NOMINAL` is the default; `DEGR` triggers under 5G free or >70% memory used; `CRIT` triggers under 1G free or >90% memory used. Underlying numbers come from `app.services.systemd.get_system_resources`. |

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
| `branch` | string | `"main"` | Default branch the agent targets. |
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
by `agent/scripts/run-manager.sh` after the manager review phase.

**Pipeline (per `plan_only` project):**

1. `run-manager.sh` emits `plan_review_start` (→ `Run.status = plan_reviewing`) before invoking the manager review.
2. Manager review runs and writes verdicts to `run-<id>-verdicts.json`.
3. `run-manager.sh` invokes `python -m agent.plan_review_gate` for each plan_only project. The driver:
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
