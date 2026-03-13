# Claude Agent Station - Architecture

## Vision

A standalone, self-hosted autonomous Claude Code agent with a web dashboard. Runs on a Linux VM, manages multiple GitHub repositories, and provides full observability through a browser UI.

**Core idea**: Manager/Employee/Analyst agent architecture with multi-employee coordination, a web dashboard for configuration, monitoring, and log viewing, plus an intelligent task queue for issue management.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     Claude Agent Station                          │
│                                                                   │
│  ┌──────────────────┐       ┌────────────────────────────────┐   │
│  │   Agent Core      │       │        Web Dashboard            │   │
│  │                   │       │                                 │   │
│  │ ┌──────────────┐ │  wh   │  ┌─────────┐   ┌────────────┐ │   │
│  │ │  Coordinator │ │──ok──▶│  │ FastAPI  │   │  Svelte 5  │ │   │
│  │ │  (Scheduler) │ │       │  │ Backend  │   │  Frontend   │ │   │
│  │ └──┬───┬───┬───┘ │       │  └────┬─────┘   └──────┬─────┘ │   │
│  │    │   │   │      │       │       │                │       │   │
│  │ ┌──▼┐ ┌▼──┐┌▼──┐ │       │       │          served by     │   │
│  │ │E1 │ │E2 ││E3 │ │       │       │           FastAPI      │   │
│  │ │   │ │   ││   │ │       │       │                │       │   │
│  │ └───┘ └───┘└───┘ │       │  ┌────▼────────────────▼────┐  │   │
│  │  Multi-Employee   │       │  │       SQLite DB (WAL)     │  │   │
│  │  + Manager Review │       │  │  projects, runs, queue,   │  │   │
│  │  + Analyst Mode   │       │  │  plans, tasks, config     │  │   │
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
│   ├── prompts/                    # System prompts (markdown)
│   │   ├── manager.md              # Manager: reviews work, issues verdicts
│   │   ├── employee.md             # Employee: implements features/fixes
│   │   ├── analyst.md              # Analyst: code analysis mode
│   │   ├── planner.md              # Planner: creates implementation plans
│   │   ├── assigner.md             # Assigner: distributes issues
│   │   └── custom/                 # User overrides (dashboard-managed)
│   ├── scripts/
│   │   ├── run-manager.sh          # Main orchestrator (2200+ lines)
│   │   ├── circuit-breaker.sh      # Failure tracking (3-strike rule)
│   │   ├── detect_plan_usage.py    # Claude plan usage detection
│   │   └── refresh-token.py        # OAuth token refresh
│   ├── coordinator/                # Multi-employee coordinator (Python)
│   │   ├── __main__.py             # Coordinator entry point
│   │   ├── config.py               # Coordinator config dataclass
│   │   ├── dag.py                  # Task DAG and dependency graph
│   │   ├── decomposer.py           # Issue → task decomposition (Haiku)
│   │   ├── employee_runner.py      # Async subprocess employee spawning
│   │   ├── guidance.py             # Manager → employee guidance channel
│   │   ├── manager.py              # Plan usage + rate limit awareness
│   │   ├── reporter.py             # Webhook event posting
│   │   ├── scheduler.py            # DAG-based concurrent scheduler
│   │   └── stream_monitor.py       # Real-time stream file monitoring
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
│   │   │   ├── models.py           # ORM models (9 tables)
│   │   │   ├── schemas.py          # Pydantic request/response schemas
│   │   │   ├── dependencies.py     # FastAPI dependency injection
│   │   │   ├── middleware/
│   │   │   │   └── auth.py         # API key authentication middleware
│   │   │   ├── routers/            # 16 API routers
│   │   │   │   ├── analytics.py    # Token usage charts, verdicts
│   │   │   │   ├── config_router.py# Agent configuration CRUD
│   │   │   │   ├── coordinator.py  # DAG, tasks, guidance API
│   │   │   │   ├── events.py       # SSE real-time event stream
│   │   │   │   ├── health.py       # Health check endpoint
│   │   │   │   ├── logs.py         # WebSocket log streaming + search
│   │   │   │   ├── oauth.py        # Claude OAuth PKCE flow
│   │   │   │   ├── plans.py        # Implementation plan management
│   │   │   │   ├── plan_usage.py   # Plan tier usage tracking
│   │   │   │   ├── prompts.py      # System prompt management
│   │   │   │   ├── projects.py     # Project CRUD
│   │   │   │   ├── queue.py        # Task queue management
│   │   │   │   ├── runs.py         # Run history, diffs, triggers
│   │   │   │   ├── system.py       # systemd + auth status
│   │   │   │   └── webhook.py      # Agent event ingestion
│   │   │   └── services/           # Business logic
│   │   │       ├── config_sync.py  # JSON ↔ DB bidirectional sync
│   │   │       ├── diff_parser.py  # Git diff parsing
│   │   │       ├── event_bus.py    # In-memory pub/sub for SSE
│   │   │       ├── idempotency.py  # Webhook deduplication
│   │   │       ├── log_importer.py # Historical log ingestion
│   │   │       ├── log_parser.py   # Stream JSONL parsing
│   │   │       ├── log_streamer.py # File tailing for WebSocket
│   │   │       ├── notifier.py     # Slack/Discord/Telegram webhooks
│   │   │       ├── stale_run_reaper.py # Orphan run recovery
│   │   │       └── systemd.py      # systemctl wrapper
│   │   ├── tests/                  # 21 test files, 325+ tests
│   │   ├── migrations/             # Config schema migrations
│   │   ├── requirements.txt        # Runtime dependencies
│   │   └── requirements-dev.txt    # Dev/test dependencies
│   │
│   └── frontend/                   # Svelte 5 SPA
│       ├── src/
│       │   ├── App.svelte          # Root + hash-based routing
│       │   ├── pages/              # 4 page components
│       │   │   ├── CommandCenterPage.svelte  # Overview dashboard
│       │   │   ├── WorkStreamPage.svelte     # Run history + details
│       │   │   ├── DecisionsPage.svelte      # Verdict review
│       │   │   └── ConfigPage.svelte         # Settings + system
│       │   ├── components/         # 38 reusable components
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
├── .github/workflows/ci.yml       # GitHub Actions CI/CD
├── pyproject.toml                  # Project config (pytest, ruff, coverage)
├── install.sh                      # One-command installer
├── ARCHITECTURE.md                 # This file
├── CLAUDE.md                       # Project conventions
└── README.md
```

---

## Database Schema (9 tables)

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

---

## Security

| Layer | Mechanism |
|-------|-----------|
| **API authentication** | Optional API key via `STATION_API_KEY` env var. Bearer token or query parameter. Health/webhook/SSE endpoints always public. |
| **Webhook authentication** | Optional shared secret via `STATION_WEBHOOK_SECRET`. X-Webhook-Token header. |
| **Event idempotency** | In-memory deduplication with TTL (prevents replay attacks) |
| **Path traversal** | `os.path.realpath()` + `startswith()` checks on log endpoints |
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

| Variable | Default | Description |
|----------|---------|-------------|
| `STATION_DB_PATH` | `/opt/git/.../station.db` | SQLite database path |
| `STATION_LOG_DIR` | `/var/log/claude-agent` | Agent log directory |
| `STATION_CONFIG_PATH` | `~/.claude/.../manager-config.json` | Agent config JSON |
| `STATION_WORKSPACES_DIR` | `/home/claude-agent/workspaces` | Git workspace root |
| `STATION_API_KEY` | (none) | API authentication key |
| `STATION_WEBHOOK_SECRET` | (none) | Webhook authentication token |
| `STATION_CREDENTIALS_PATH` | `~/.claude/.credentials.json` | Claude CLI credentials |
| `STATION_ALLOWED_ORIGINS` | localhost:5173,4173 | CORS allowed origins |

---

## Tech Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Agent orchestration | Bash + Claude CLI | Existing, proven, works |
| Multi-employee coordinator | Python asyncio | Concurrent task scheduling |
| Backend API | Python 3.11+ / FastAPI | Async, auto-docs, lightweight |
| Database | SQLite (WAL mode) | Zero config, sufficient for scale |
| Frontend | Svelte 5 + Vite + TailwindCSS | Tiny bundle, no runtime |
| Real-time updates | SSE (event bus) + WebSocket (logs) | Browser-native, no deps |
| Process management | systemd | Native to target platform |
| CI/CD | GitHub Actions | pytest + ruff + frontend build |
| Linting | ruff | Fast Python linter/formatter |
| Testing | pytest + pytest-asyncio | 325+ tests, async support |
