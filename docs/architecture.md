# Claude Agent Station - Architecture

## Vision

A standalone, self-hosted autonomous Claude Code agent with a web dashboard. Runs on a Linux VM, manages multiple GitHub repositories, and provides full observability through a browser UI.

**Core idea**: Agent Teams architecture powered by Claude Agent SDK. A lead agent coordinates three role-specialized teammates (`backend`, `frontend`, `qa`) working in isolated worktrees; eligible issues are decomposed into tasks and distributed across them by specialty. A separate manager review phase issues verdicts. Web dashboard provides real-time visibility into team activity.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     Claude Agent Station                          │
│                                                                   │
│  ┌──────────────────┐       ┌────────────────────────────────┐   │
│  │   Agent Teams     │       │        Web Dashboard            │   │
│  │                   │  wh   │                                 │   │
│  │ ┌──────────────┐ │──ok──▶│  ┌─────────┐   ┌────────────┐ │   │
│  │ │  Lead Agent  │ │       │  │ FastAPI  │   │  Svelte 5  │ │   │
│  │ │  (Sonnet)    │ │       │  │ Backend  │   │  Frontend   │ │   │
│  │ └──┬───┬───┬───┘ │       │  └────┬─────┘   └──────┬─────┘ │   │
│  │    │   │   │      │       │       │                │       │   │
│  │ ┌──▼┐ ┌▼──┐┌▼──┐ │       │       │          served by     │   │
│  │ │T1 │ │T2 ││T3 │ │       │       │           FastAPI      │   │
│  │ │   │ │   ││   │ │       │       │                │       │   │
│  │ └───┘ └───┘└───┘ │       │  ┌────▼────────────────▼────┐  │   │
│  │  Teammates (Opus) │       │  │       SQLite DB (WAL)     │  │   │
│  │  + Manager Review │       │  │  projects, runs, queue,   │  │   │
│  │                   │       │  │  plans, tasks, config     │  │   │
│  │                   │       │  └──────────────────────────┘  │   │
│  └─────┬─────────────┘       │                                 │   │
│        │                     │  ┌──────────────────────────┐  │   │
│  ┌─────▼─────────┐          │  │  SSE Event Bus (real-time)│  │   │
│  │    systemd     │          │  └──────────────────────────┘  │   │
│  │  timer+service │          └────────────────────────────────┘   │
│  └───────────────┘                                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
claude-agent-station/
├── agent/                          # Autonomous agent core
│   ├── agents/                     # Agent Teams definitions
│   │   ├── issue-worker.md         # Teammate: implements a single issue
│   │   └── manager.md              # Manager sibling: reviews work, issues verdicts
│   ├── conflict_resolver/        # Layered conflict resolver (LLM + mechanical)
│   │   ├── __main__.py           # python -m agent.conflict_resolver entrypoint
│   │   ├── budget.py             # rolling 24h token budget
│   │   ├── markers.py            # git conflict marker parser
│   │   ├── prompts.py            # prompt assembly
│   │   └── sdk_runner.py         # Claude Agent SDK wrapper
│   ├── prompts/                    # System prompts (markdown)
│   │   ├── analyst.md              # Analyst role prompt
│   │   ├── assigner.md             # Assigner role prompt
│   │   ├── employee.md             # Employee role prompt
│   │   ├── manager.md              # Manager: reviews work, issues verdicts
│   │   ├── planner.md              # Planner role prompt
│   │   ├── REPORT-SCHEMAS.md       # Structured report schemas
│   │   ├── reviewer.md             # Reviewer role prompt
│   │   ├── roles/                  # Persona overlays (architect, designer, …)
│   │   ├── security-reviewer.md    # Security reviewer prompt
│   │   ├── triager.md              # Triager role prompt
│   │   ├── vision_create.md        # Vision creation prompt
│   │   └── vision_refine.md        # Vision refinement prompt
│   ├── scripts/
│   │   ├── circuit-breaker.sh      # Failure tracking (3-strike rule)
│   │   ├── detect_plan_usage.py    # Claude plan usage detection
│   │   ├── integration-branch.sh   # Integration branch management
│   │   ├── lib/                    # Shared shell library
│   │   │   └── conflict-helpers.sh  # Shared bash helpers
│   │   ├── promote.sh              # Branch promotion helper
│   │   ├── refresh-token.py        # OAuth token refresh
│   │   ├── resolve-conflicts.sh  # Phase orchestrator (mechanical → lockfile → LLM)
│   │   ├── sprint-cycle.sh         # Sprint cycle automation
│   │   └── tests/                  # Script unit tests
│   ├── skills/                     # Reusable agent skills
│   ├── audit_hook.py               # SDK PreToolUse/PostToolUse audit hook
│   ├── auto_mode.py                # Autonomous mode controller
│   ├── launcher.py                 # Agent launch helper
│   ├── run_control.py              # Run pause/resume/stop control
│   ├── station_orchestrator.py     # Agent Teams orchestrator (Claude Agent SDK)
│   ├── tray_referral.py            # Tray notification referral
│   ├── vision.py                   # Vision pipeline entry point
│   ├── vision_analyst.py           # Vision analysis worker
│   ├── vision_scoring.py           # Vision scoring logic
│   ├── systemd/                    # Service definitions
│   ├── selinux/                    # SELinux policy
│   └── config/                     # Default configuration template
│
├── dashboard/
│   ├── backend/                    # FastAPI application
│   │   ├── app/
│   │   │   ├── main.py             # App, lifespan, router registration
│   │   │   ├── config.py           # pydantic-settings (STATION_ prefix)
│   │   │   ├── database.py         # Async SQLAlchemy + migrations
│   │   │   ├── models.py           # ORM models (20 tables)
│   │   │   ├── schemas.py          # Pydantic request/response schemas
│   │   │   ├── dependencies.py     # FastAPI dependency injection
│   │   │   ├── middleware/
│   │   │   │   └── auth.py         # API key authentication middleware
│   │   │   ├── routers/            # 21 API routers
│   │   │   │   ├── agent_events.py # Workflow event log: append + query API
│   │   │   │   ├── analytics.py    # Token usage charts, verdicts
│   │   │   │   ├── audit.py        # Audit log query API
│   │   │   │   ├── config_router.py# Agent configuration CRUD
│   │   │   │   ├── coordinator.py  # DAG, tasks, guidance API
│   │   │   │   ├── events.py       # SSE real-time event stream
│   │   │   │   ├── github_app.py   # GitHub App installation
│   │   │   │   ├── github_webhook.py # GitHub webhook intake
│   │   │   │   ├── health.py       # Health check endpoint
│   │   │   │   ├── logs.py         # WebSocket log streaming + search
│   │   │   │   ├── oauth.py        # Claude OAuth PKCE flow
│   │   │   │   ├── permissions.py  # Permission request management
│   │   │   │   ├── plans.py        # Implementation plan management
│   │   │   │   ├── plan_usage.py   # Plan tier usage tracking
│   │   │   │   ├── prompts.py      # System prompt management
│   │   │   │   ├── projects.py     # Project CRUD
│   │   │   │   ├── queue.py        # Task queue management
│   │   │   │   ├── runs.py         # Run history, diffs, triggers
│   │   │   │   ├── system.py       # systemd + auth status
│   │   │   │   ├── vision.py       # Vision chat sessions
│   │   │   │   └── webhook.py      # Run event ingest from station_orchestrator
│   │   │   └── services/           # Business logic
│   │   │       ├── adapters/       # Notifier adapters (Slack, Discord, …)
│   │   │       ├── adaptive_scheduler.py   # Dynamic scheduling
│   │   │       ├── audit_retention.py      # Audit log retention policy
│   │   │       ├── backpressure.py         # Queue backpressure control
│   │   │       ├── config_sync.py          # JSON ↔ DB bidirectional sync
│   │   │       ├── coordinator_service.py  # Coordinator business logic
│   │   │       ├── diff_parser.py          # Git diff parsing
│   │   │       ├── event_bus.py            # In-memory pub/sub for SSE
│   │   │       ├── github_app.py           # GitHub App API client
│   │   │       ├── github_contents.py      # GitHub file content fetching
│   │   │       ├── github_pat.py           # GitHub PAT management
│   │   │       ├── idempotency.py          # Webhook deduplication
│   │   │       ├── log_importer.py         # Historical log ingestion
│   │   │       ├── log_parser.py           # Stream JSONL parsing
│   │   │       ├── log_streamer.py         # File tailing for WebSocket
│   │   │       ├── notifier.py             # Slack/Discord/Telegram webhooks
│   │   │       ├── queue_service.py        # Task queue business logic
│   │   │       ├── run_lifecycle.py        # Run state machine
│   │   │       ├── service_control.py      # systemd service control
│   │   │       ├── stale_run_reaper.py     # Orphan run recovery
│   │   │       ├── systemd.py              # systemctl wrapper
│   │   │       ├── vision_chat.py          # Vision chat session logic
│   │   │       ├── vision_chat_parser.py   # Vision chat message parsing
│   │   │       ├── vision_cleanup.py       # Vision session cleanup
│   │   │       └── vision_render.py        # Vision output rendering
│   │   ├── tests/                  # 61 test files, 929 tests
│   │   ├── migrations/             # Config schema migrations
│   │   ├── requirements.txt        # Runtime dependencies
│   │   └── requirements-dev.txt    # Dev/test dependencies
│   │
│   └── frontend/                   # Svelte 5 SPA
│       ├── src/
│       │   ├── App.svelte          # Root + hash-based routing
│       │   ├── pages/              # 8 page components
│       │   │   ├── AgentTeamsCanvas.svelte  # Agent Teams live view
│       │   │   ├── CommandCenter.svelte     # Dispatch — Pro dense board (strip + ticker + filters + telemetry + run table + right rail)
│       │   │   ├── MissionControl.svelte    # Mission control panel
│       │   │   ├── ProjectDetail.svelte     # Single-project view
│       │   │   ├── ProjectsPage.svelte      # Projects list
│       │   │   ├── QueueBoard.svelte        # Task queue kanban
│       │   │   ├── RunDetail.svelte         # Run detail + diffs
│       │   │   └── SettingsPage.svelte      # Settings + system (incl. Audit tab)
│       │   ├── components/         # 56 reusable components (grouped by domain)
│       │   └── lib/                # TypeScript modules
│       │       ├── api.ts          # API client (typed, with auth + timeout)
│       │       ├── types.ts        # TypeScript interfaces
│       │       ├── event-stream.ts # SSE client (exponential backoff)
│       │       ├── ws.ts           # WebSocket client (exponential backoff)
│       │       ├── router.svelte.ts# Hash-based SPA router
│       │       ├── toast.svelte.ts # Toast notification system
│       │       ├── format.ts       # Date/number formatting
│       │       ├── log-parser.ts   # Log event parsing
│       │       ├── agent-presence.svelte.ts  # Real-time agent tracking
│       │       └── workspace-renderer.ts     # Workspace visualization
│       └── package.json
│
├── docs/
│   ├── adr/                        # Architecture Decision Records
│   ├── architecture.md             # This file
│   ├── prototypes/                 # Prototype docs
│   └── superpowers/                # Agent skill documentation
│
├── .github/workflows/ci.yml       # GitHub Actions CI/CD
├── pyproject.toml                  # Project config (pytest, ruff, coverage)
├── install.sh                      # One-command installer
├── ARCHITECTURE.md                 # Stub → docs/architecture.md
├── CLAUDE.md                       # Project conventions
└── README.md
```

---

## Database Schema (20 tables)

| Table | Purpose | Key fields |
|-------|---------|------------|
| `projects` | Managed GitHub repositories | repo, priority, mode, enabled, branch |
| `runs` | Execution history | run_id, status, verdict, tokens, trace_id |
| `config` | Key-value settings store | key, value (JSON) |
| `plans` | Implementation plans | title, steps, status, files_affected |
| `coordinator_tasks` | DAG task records | task_id, run_id, status, depends_on, tokens_total, turns |
| `coordinator_messages` | Guidance/conflict messages | direction, message_type, content |
| `notifications` | Run completion alerts | type (approve/reject/pr/error) |
| `task_queue` | Work queue with state machine | state, priority, retry_count |
| `plan_usage_history` | Token usage tracking | plan_tier, weekly_tokens_used |
| `audit_log` | Append-only action audit (per tool call) | run_id, actor, action_kind, action_detail, status |
| `agent_events` | Structured audit trail (ESAA) | workflow_id, agent_id, event_type |
| `task_outcomes` | Adaptive scheduling learning | mode_used, model_used, success |
| `brainstorm_sessions` | AI brainstorm conversations | project_id, persona, title |
| `brainstorm_messages` | Brainstorm chat messages | session_id, role, content |
| `integration_features` | Features merged to integration (dev) branch | project_repo, branch, state, validation_status |
| `prompt_versions` | Prompt A/B testing | prompt_name, version, content_hash |
| `permission_requests` | Agent permission request queue | agent_id, action, status |
| `run_controls` | Run pause/resume/stop signals | run_id, action, payload |
| `station_control` | Singleton row holding global intervention flags | global_pause, updated_at, updated_by |
| `vision_chat_sessions` | Vision pipeline chat sessions | project_id, session state |

---

## Security

| Layer | Mechanism |
|-------|-----------|
| **API authentication** | Optional API key via `STATION_API_KEY` env var. Bearer token or query parameter. Health and webhook endpoints always public. SSE requires API key when configured. |
| **Webhook authentication** | Optional shared secret via `STATION_WEBHOOK_SECRET`. X-Webhook-Token header. |
| **Event idempotency** | In-memory deduplication with TTL (prevents replay attacks) |
| **Path traversal** | `os.path.realpath()` + `Path.is_relative_to()` checks on log endpoints |
| **XSS prevention** | DOMPurify for markdown rendering, Svelte auto-escaping |
| **CORS** | Explicit origin whitelist (no wildcard with credentials) |
| **OAuth** | PKCE flow with TTL state parameter |
| **Input validation** | Config key whitelist, Pydantic schema validation |
| **Subprocess safety** | 30-second timeouts on all git subprocess calls |

---

## Development

```bash
# Backend
cd dashboard/backend
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8420

# Frontend
cd dashboard/frontend
npm install
npm run dev

# Run tests
cd dashboard/backend && python -m pytest tests/ -v

# Lint
ruff check dashboard/backend/

# Test coverage
python -m pytest tests/ --cov=app --cov-report=term-missing
```

### Environment Variables

See [`configuration.md`](configuration.md#environment-variables) for the full table of `STATION_*` settings, defaults, and descriptions.

---

## Tech Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Agent orchestration | Bash + Claude Agent SDK | Agent Teams for parallel work |
| Backend API | Python 3.11+ / FastAPI | Async, auto-docs, lightweight |
| Database | SQLite (WAL mode) | Zero config, sufficient for scale |
| Frontend | Svelte 5 + Vite + TailwindCSS | Tiny bundle, no runtime |
| Real-time updates | SSE (event bus) + WebSocket (logs) | Browser-native, no deps |
| Process management | systemd | Native to target platform |
| CI/CD | GitHub Actions | pytest + ruff + frontend build |
| Linting | ruff | Fast Python linter/formatter |
| Testing | pytest + pytest-asyncio | 929 tests, async support |

---

## Run Modes

The orchestrator dispatches runs in different modes, each with distinct behavior and responsibilities:

### Project mode (per-project, set via UI)

Each project picks one of four modes (`Project.mode`). The Agent Teams
orchestrator branches on this value to shape both the spawn prompt and
the manager review package — see [`configuration.md` §
Project mode](configuration.md#project-mode) for the full table and
[`agent/prompts/manager.md`](../agent/prompts/manager.md) for the
review-criteria branching.

- **`full`** — plan + implement + push branch. Manager review under Full Mode Review.
- **`analyze`** — read-only investigation; teammates write findings to a report file. Reviewed under Analyze Mode Review.
- **`plan`** — plan-quality output, source untouched. Reviewed under Plan Mode Review.
- **`plan_only`** — pre-implementation gate. Teammates write a plan and stop. Reviewed under Plan Review Mode (`APPROVE_PLAN` / `REVISE_PLAN` / `REJECT_PLAN`). On approve, a follow-up `full` run is enqueued referencing the approved plan.

### Run-level run kinds

- **Agent Teams flow** (the default for `full`/`analyze`/`plan`/`plan_only`) — lead decomposes eligible issues into tasks, spawns three teammates in isolated worktrees, then spawns a fourth `manager` sibling after teammates finish. The manager reviews each implementation and writes verdicts (APPROVE/PR/REJECT for `full`; APPROVE_PLAN/REVISE_PLAN/REJECT_PLAN for `plan_only`). Both `issue-worker.md` and `manager.md` live under `agent/agents/` and are loaded by the orchestrator at startup. Manager tokens flow through `handle_stream_event` via `AssistantMessage.usage` in the same session as lead + teammates (#390).
- **`vision-bootstrap`** — single-shot run that dispatches `agent/vision_analyst.py` to propose new issues from `docs/vision.md`. Triggered automatically (orchestrator empty backlog, or vision commit with content-hash change) or manually from the Vision tab. Never spawns teammates, never opens PRs.
- **`fix`** — single-issue repair mode for regressions and urgent bugs (legacy; not exposed in the project mode dropdown).
- **`triage`** — issue classification and labeling without implementation (legacy).
- **`review`** — security or code review mode for pull requests (legacy).

#### Manager paths sidecar (`.claude-manager-paths.json`, #411)

The orchestrator writes a structured JSON sidecar to the per-project
workspace root **before** the lead's first turn. The manager sibling
`Read`s this file on its first turn to discover its review-package and
verdicts-file paths instead of parsing them out of the lead's spawn-prompt
markdown — eliminating the path-drift failure mode that previously could
send the manager's verdicts write to a wrong path and silently lose every
teammate's work for the run.

| Field | Type | Source |
|---|---|---|
| `review_package` | absolute path string | `<log_dir>/run-<id>-review.md` (orchestrator-owned) |
| `verdicts_file` | absolute path string | `<log_dir>/run-<id>-verdicts.json` (orchestrator-owned) |
| `hard_deadline_turns` | int | `config.limits.max_manager_turns` (default 30; SDK frontmatter ceiling is 60) |
| `soft_deadline_turns` | int | `max(1, hard_deadline_turns // 2)` |

The sidecar is workspace-scoped because the manager sibling's CWD inside
the SDK session is the workspace. The lead's spawn-prompt instructs the
manager to `Read .claude-manager-paths.json` first and does **not**
interpolate the paths into the prose; `agent/agents/manager.md` `<context>`
mirrors the contract on the manager side. If the sidecar is missing or
unparseable the manager refuses to guess paths — the orchestrator's
`manager_no_verdicts` webhook + `exit_code=6` path then fires and surfaces
the failure for triage.

See `_write_manager_paths_sidecar` in `agent/station_orchestrator.py` and
the `# --- #411` test block in
`dashboard/backend/tests/test_manager_sibling.py`.

### Sibling-teammate coordination (#456)

The lead agent writes `.claude-team-contracts.md` to the workspace
root before spawning the three role-specialized teammates
(backend / frontend / qa). The file documents cross-team contracts:

| Section | Purpose |
|---|---|
| API Routes | Method, path, owning role, response shape per route |
| Field Names | canonical_key → chosenName mappings |
| Response Shapes | route_path → response shape description |
| Enum Values | enum_name → allowed value list |
| Route Ownership | route_path → owning role |

Each teammate's spawn prompt includes a READ-FIRST instruction
pointing at this file. Manager review treats the contract as binding;
verdicts that violate it should be REJECT.

**Cross-run feedback (#456):** Before building the lead's prompt,
`iterate_projects` globs `/var/log/claude-agent/run-*-verdicts.json`
for the most recent file containing verdicts whose `project` field
matches the current project's repo. If found, a short prose summary
is folded into the lead's prompt as a "Recent verdicts (last run on
this project)" section. The lead resolves the prior conflicts in
the new contracts file.

**Advisory validator:** `agent/team_contracts.py::validate_verdict_against_contracts`
scans each manager verdict's `reasoning` text for contract violations
(field-name mismatch, route-ownership conflict, enum-value drift,
test-assertion drift).
The test-assertion drift check (#458) flags `"test expects X"`
patterns where X isn't in the contract's Response Shapes section AND
a divergence signal (`will break`, `after merge`, etc.) follows.
Violations are logged at WARNING; verdicts are NOT auto-flipped —
the manager has final say. The validator is heuristic by design
(string matching against the manager's prose), not a full code parser.

Failure modes:
- No `contracts.md` written → degrades to pre-#456 behavior with a
  WARNING log.
- Malformed file → parser returns `None`; same fallback as missing.
- No prior verdicts file → no injection; first-ever run behavior.
- `plan_only` mode → no siblings spawned, contracts instruction
  omitted from the lead's prompt.

### Plan-review gate

The `plan_only` mode adds a manual checkpoint between plan-writing and
implementation. The gate is implemented by `agent/plan_review_gate.py`
and invoked by `agent/iterate_projects` after the manager review
phase. Run-status transitions are driven by webhook events handled in
`app/services/run_lifecycle.py`.

```
plan_only employee writes plan
   │  (Run.status = running)
   ▼
iterate_projects emits plan_review_start
   │  (Run.status = plan_reviewing)
   ▼
manager reviews plan → run-<id>-verdicts.json
   │
   ▼
iterate_projects: python -m agent.plan_review_gate
   │  emits awaiting_plan_review
   │  (Run.status = awaiting_plan_review)
   │
   ├── APPROVE_PLAN → POST /api/queue { mode: "full", context.approved_plan_path }
   │                  → emit plan_approved (Run.status = plan_approved, finished_at set)
   │                  → next cycle picks up the follow-up full run
   │
   ├── REVISE_PLAN within budget → write feedback file, stay at awaiting_plan_review
   │                               (TODO: live re-spawn loop)
   │
   ├── REVISE_PLAN past STATION_PLAN_REVISION_MAX → emit plan_rejected
   │
   └── REJECT_PLAN → emit plan_rejected (Run.status = plan_rejected, finished_at set)
```

If the queue POST fails the run stays in `awaiting_plan_review` instead
of flipping to `plan_approved` — losing the approved plan would be
worse than leaving the gate engaged for manual recovery. The dashboard
surfaces every gate state via banners on Mission Control and the Agent
Teams canvas. See `docs/configuration.md#plan-review-gate-plan_only-only`
for the full env-var matrix and verdict-action table.

### Run status values (`runs.status`)

Terminal states written to the `runs` table and mapped by `app/services/run_lifecycle.py`:

| Status | Meaning |
|---|---|
| `running` | Run is active; orchestrator in progress. |
| `completed` | Run finished with work attempted and all projects resolved (agent-side values `success`, `finished`, `no_reports`, `completed`, `rate_limited` all map here). |
| `skipped` | Run finished cleanly with no eligible work to do in any configured project. Distinct from `failed`. Introduced 2026-05-17 (#446 / #447). |
| `failed` | Run encountered a genuine error (e.g. manager produced no verdicts after work was attempted, orchestrator exception). |
| `interrupted` | Run was stopped externally via a `run_controls` signal. |
| `plan_reviewing` | `plan_only` run: manager is reviewing the plan. |
| `awaiting_plan_review` | `plan_only` run: gate is open, waiting for human approval. |
| `plan_approved` | `plan_only` run: plan approved; follow-up `full` run enqueued. Terminal. |
| `plan_rejected` | `plan_only` run: plan rejected or revision budget exhausted. Terminal. |

### Webhook events (agent → dashboard)

Events posted to `POST /api/webhook` by `agent/webhook_emitter.py` and routed in `dashboard/backend/app/routers/webhook.py`:

| Event | Description |
|---|---|
| `run_start` | Run has begun; creates or reactivates the `runs` row. |
| `run_complete` | Run has finished; sets terminal status and `finished_at`. |
| `manager_no_verdicts` | Manager was spawned but produced no verdicts file (genuine failure). Payload includes `exit_code=6`. Distinct from `project_skipped_no_work`. |
| `project_skipped_no_work` | Emitted per-project when the orchestrator found no eligible work and did not open the SDK session. Payload: `{project, reason}` where `reason` is `"no_eligible_work"`. Run-level analogue: `runs.status="skipped"`. Introduced 2026-05-17 (#447). |
| `plan_review_start` | `plan_only`: teammates have written a plan; manager review begins. |
| `awaiting_plan_review` | `plan_only`: gate is open; waiting for human decision. |
| `plan_approved` | `plan_only`: human approved the plan. |
| `plan_rejected` | `plan_only`: plan rejected or revision budget exhausted. |

### Per-project digest decision values

Each project entry in the run digest carries a `decision` field written by `iterate_projects`:

| Decision | Meaning |
|---|---|
| `APPROVE` | Manager approved the implementation; branch ready for merge. |
| `PR` | Manager opened a pull request. |
| `REJECT` | Manager rejected the implementation; no PR opened. |
| `SKIP` | Project had no eligible work; no SDK session was opened. Paired with the `project_skipped_no_work` webhook. |
| `ERROR` | Orchestrator or manager encountered an error while processing the project. |

---

## Deployment Model

### Hardlink Deployment

The project uses two directory paths that point to the same underlying files:

- `/opt/git/claude-agent-station/` — the git repository (used for development)
- `/opt/claude-agent-station/` — the deployment path (referenced by systemd services)

These are hardlinked, so changes in either path are immediately visible at the other.

### Python Virtual Environment

The venv at `<project-root>/venv/` (Python 3.12) is shared across both paths. Scripts reference it via relative paths:

- `station_orchestrator.py`: invoked via the same venv python (`$agent_dir/../venv/bin/python3`)
- systemd dashboard service: `<project-root>/venv/bin/uvicorn`

This resolution is consistent because the hardlink ensures both paths resolve to the same physical venv.

## Run Timeline API

`GET /api/runs/{run_id}/timeline` returns a chronologically merged event
stream for a single run, drawn from five sources:

| Source table | Kind | Notes |
|---|---|---|
| `runs` (+ `agent_events` with `event_type LIKE 'lifecycle.%'`) | `lifecycle` | `run_start` / `run_complete` synthesised from `started_at` / `finished_at`. |
| `audit_log` | `tool` | One event per row, `event` = `{action_kind}.{status}`. `data.stdout_tail` / `stderr_tail` trimmed to 1 KB. |
| `coordinator_tasks` | `teammate` | `teammate.spawned` at `claimed_at`, `teammate.completed` at terminal `finished_at`. |
| `agent_events` (`event_type IN verdict_execute, manager_review, manager_review_complete`) | `verdict` | Manager-decision events. |
| `conflict_resolutions` (matched by run's branch) | `conflict` | `conflict.started` + `conflict.{outcome}`. |

Pagination is cursor-based on `(t, source, source_id)`. Filter via `?kinds=`
(comma-separated subset). Full payloads remain available via
`/api/audit?run_id=…&id=…` for `tool` events whose tails are truncated.

Implementation: `dashboard/backend/app/services/run_timeline.py`.

## Per-run containers (#386)

The Agent Teams runtime now uses two container roles instead of one:

- **cas-launcher** — long-lived FastAPI service in the `agent` compose container. Exposes `/run`, `/status`, `/stop`, `/webhook-tick`, `/vision-analyst`. Mounts the Docker socket and owns the `_runners: dict[str, RunnerHandle]` map keyed by `run_id`. Idempotent — a duplicate `/run` for the same `hint_run_id` returns 409 instead of forking a second container.
- **cas-runner-`<run-id>`** — ephemeral container, one per active run, spawned by the launcher via the Docker socket. Same image as the launcher (`claude-agent-station/agent:dev`). Entry point: `python -m agent.station_orchestrator --driver --run-id …`. Started with `--init` for proper PID 1 signal handling and `--rm` for auto-cleanup on exit; resource budgets come from the Project's `runner_memory_limit` / `runner_cpu_limit` columns (`mem_limit` + `nano_cpus` on `containers.run`).

Both roles join the `agent-net` bridge network so the runner can reach the dashboard at `http://dashboard:8420` for webhook callbacks. Workspace state remains durable: every runner mounts the shared `station-data` named volume, so consecutive runs on the same project continue to see the same `workspaces/<repo>/` tree.

Two concurrent runs on different projects no longer share a process tree, SDK CLI subprocess, memory, or CPU budget — Docker assigns each container its own PID namespace and cgroup. The `launcher_reaper` watchdog enforces a heartbeat-based liveness check and force-stops runners that miss too many `/webhook-tick` updates.

Implementation: `agent/runner_spawn.py` (spawn + name + quotas), `agent/launcher.py` (`/run`, `/stop`, `_runners` map), `agent/launcher_reaper.py` (stale-runner reaper).

## Issue decomposition

The coordinator's `decide.py` runs a pre-dispatch hook `maybe_run_splitter`
(feature-gated by `STATION_SPLIT_ENABLED=1`) before spawning a specialist
team. Eligible issues — long bodies, ≥4 acceptance criteria, cross-cutting
label sets, or the explicit `split-me` label — are routed to an
issue-splitter SDK session (`agent/issue_splitter/runner.py`). The splitter
emits a JSON array of 2-5 sub-issue proposals; the harness creates them on
GitHub with a `splitter-proposed` label and `Parent: #N` back-link.

Sub-runs execute concurrently when per-project containers (#386) are in
place: one runner container per sub-run, all merging to an
`integration/issue-<N>` branch. CI on the integration branch is the
integration test. A single PR to `dev` is opened once all sub-runs land.

Failure isolation: a failed sub-run does not block its siblings; the
parent stays open with a `splitter-needs-rework` label only if *every*
sub-run fails.
