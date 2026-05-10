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
│   │   └── issue-worker.md         # Teammate: implements a single issue
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
│   │   ├── run-manager.sh          # Entry point + manager review phase
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
│   │   │   │   └── webhook.py      # Run event ingest from run-manager.sh
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
| `coordinator_tasks` | DAG task records | task_id, run_id, status, depends_on |
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

- **Agent Teams flow** (the default for `full`/`analyze`/`plan`/`plan_only`) — lead decomposes eligible issues into tasks, spawns three teammates in isolated worktrees, manager reviews each implementation, verdicts issued (APPROVE/PR/REJECT for `full`; APPROVE_PLAN/REVISE_PLAN/REJECT_PLAN for `plan_only`).
- **`vision-bootstrap`** — single-shot run that dispatches `agent/vision_analyst.py` to propose new issues from `docs/vision.md`. Triggered automatically (orchestrator empty backlog, or vision commit with content-hash change) or manually from the Vision tab. Never spawns teammates, never opens PRs.
- **`fix`** — single-issue repair mode for regressions and urgent bugs (legacy; not exposed in the project mode dropdown).
- **`triage`** — issue classification and labeling without implementation (legacy).
- **`review`** — security or code review mode for pull requests (legacy).

### Plan-review gate

The `plan_only` mode adds a manual checkpoint between plan-writing and
implementation. The gate is implemented by `agent/plan_review_gate.py`
and invoked by `agent/scripts/run-manager.sh` after the manager review
phase. Run-status transitions are driven by webhook events handled in
`app/services/run_lifecycle.py`.

```
plan_only employee writes plan
   │  (Run.status = running)
   ▼
run-manager.sh emits plan_review_start
   │  (Run.status = plan_reviewing)
   ▼
manager reviews plan → run-<id>-verdicts.json
   │
   ▼
run-manager.sh: python -m agent.plan_review_gate
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

---

## Deployment Model

### Hardlink Deployment

The project uses two directory paths that point to the same underlying files:

- `/opt/git/claude-agent-station/` — the git repository (used for development)
- `/opt/claude-agent-station/` — the deployment path (referenced by systemd services)

These are hardlinked, so changes in either path are immediately visible at the other.

### Python Virtual Environment

The venv at `<project-root>/venv/` (Python 3.12) is shared across both paths. Scripts reference it via relative paths:

- `run-manager.sh`: `$agent_dir/../venv/bin/python3`
- `station_orchestrator.py`: invoked via the same venv python
- systemd dashboard service: `<project-root>/venv/bin/uvicorn`

This resolution is consistent because the hardlink ensures both paths resolve to the same physical venv.
