# Configuration

*Reference for every configurable setting. For operators tuning the system.*

## Where config lives

The canonical configuration store is the `config` table in `station.db` (key/value, JSON-encoded values). The dashboard writes here directly. A JSON view of the same config is materialised at `STATION_CONFIG_PATH` for the agent process to read; the dashboard's `config_sync` service keeps the two in sync. **Always edit through the dashboard or the `/api/config` endpoint** — direct edits to the JSON file are overwritten on the next sync.

## Environment variables

All variables use the `STATION_` prefix (set by `SettingsConfigDict(env_prefix="STATION_")`). They can also be placed in a `.env` file at the project root.

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

## Models

Defaults per role (from `agent/config/default-config.json`):

| Role | Default | Config key |
|------|---------|------------|
| Employee (Teammate) | `claude-opus-4-7` | `models.employee` |
| Manager (Lead + Manager review) | `claude-sonnet-4-6` | `models.manager` |
| Analyst | `claude-sonnet-4-6` | `models.analyst` |
| Planner | `claude-sonnet-4-6` | `models.planner` |
| Router | `claude-haiku-4-5-20251001` | `models.router` |

To change a model, set the corresponding key via the dashboard Config page or `PATCH /api/config`. The orchestrator picks up the change on the next run.

A fallback model can also be configured via `--fallback-model` on the CLI; when the primary model is throttled, the system falls back one tier (Opus 4.7 → Sonnet 4.6, Sonnet 4.6 → Haiku 4.5) rather than silently degrading the primary model.

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

Agent behaviour is gated by a per-project autonomy-level setting. See [`adr/0001-autonomy-levels.md`](adr/0001-autonomy-levels.md) for the model and the level definitions. The default for new projects is `assisted`.

## API key and webhook secret

| Setting | Purpose |
|---------|---------|
| `STATION_API_KEY` | Required for all `/api/*` requests except the health router and the internal webhook router. Pass as a Bearer token (`Authorization: Bearer <key>`) or the `?token=` query parameter (the query parameter is provided as a fallback for SSE clients that cannot set custom headers). |
| `STATION_WEBHOOK_SECRET` | Required on `POST /api/webhook/*` requests via the `X-Webhook-Token` header. Prevents external sources from injecting fake agent events. |
| `STATION_GITHUB_WEBHOOK_SECRET` | Used to verify HMAC-SHA256 signatures on incoming GitHub webhook payloads. Required when GitHub webhook integration is enabled. |

If none of these are set, the dashboard and webhook endpoints run unauthenticated — only suitable for a fully isolated host.

Exempt from `STATION_API_KEY` auth (verified in `dashboard/backend/app/main.py`): the health router, the internal agent webhook router, the GitHub webhook router, and GitHub App lifecycle endpoints.

## Project config

Each managed repository is one row in the `projects` table. The dashboard's Projects page is the easiest way to edit; the underlying schema (used by `POST /api/projects`) is:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `repo` | string | _(required)_ | GitHub repository in `owner/repo` format. |
| `priority` | string | `"medium"` | Scheduling priority: `high`, `medium`, or `low`. |
| `mode` | string | `"full"` | Agent operating mode: `full`, `analyze`, `plan`, `fix`, `triage`, or `review`. |
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
