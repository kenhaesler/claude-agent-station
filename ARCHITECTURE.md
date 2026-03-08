# Claude Agent Station - Architecture

## Vision

A standalone, self-hosted autonomous Claude Code agent with a web dashboard. Runs on a Linux VM, manages multiple GitHub repositories, and provides full observability through a browser UI.

**Core idea**: The manager/employee agent architecture from `claude-user-memory` extracted into its own project, enhanced with a web dashboard for configuration, monitoring, and log viewing.

---

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Claude Agent Station                   │
│                                                          │
│  ┌──────────────┐    ┌──────────────────────────────┐   │
│  │  Agent Core   │    │       Web Dashboard           │   │
│  │              │    │                              │   │
│  │ ┌──────────┐ │    │  ┌────────┐   ┌───────────┐ │   │
│  │ │ Manager  │ │    │  │ FastAPI│   │  Frontend  │ │   │
│  │ │ (Sonnet) │ │    │  │ Backend│   │  (Svelte)  │ │   │
│  │ └────┬─────┘ │    │  └───┬────┘   └─────┬─────┘ │   │
│  │      │       │    │      │               │       │   │
│  │ ┌────▼─────┐ │    │      │         served by     │   │
│  │ │ Employee │ │    │      │          FastAPI       │   │
│  │ │ (Opus)   │ │    │      │               │       │   │
│  │ └──────────┘ │    │  ┌───▼───────────────▼───┐   │   │
│  │              │    │  │      SQLite DB         │   │   │
│  │ ┌──────────┐ │    │  │  (config, runs, logs) │   │   │
│  │ │ Analyst  │ │    │  └───────────────────────┘   │   │
│  │ │ (Sonnet) │ │    │                              │   │
│  │ └──────────┘ │    └──────────────────────────────┘   │
│  └──────┬───────┘                                        │
│         │                                                │
│  ┌──────▼───────┐                                        │
│  │   systemd    │                                        │
│  │ timer+service│                                        │
│  └──────────────┘                                        │
└─────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
claude-agent-station/
├── agent/                      # Autonomous agent core
│   ├── prompts/                # System prompts (md files)
│   │   ├── manager.md          # Manager agent prompt
│   │   ├── employee.md         # Employee agent prompt
│   │   └── analyst.md          # Analyst agent prompt
│   ├── scripts/
│   │   ├── run-manager.sh      # Main orchestrator script
│   │   └── circuit-breaker.sh  # Circuit breaker utility
│   ├── systemd/
│   │   ├── claude-agent.service
│   │   └── claude-agent.timer
│   ├── selinux/
│   │   └── claude-agent.te     # SELinux policy module
│   └── config/
│       └── default-config.json # Default configuration template
│
├── dashboard/
│   ├── backend/                # FastAPI application
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py         # FastAPI app, serves frontend
│   │   │   ├── config.py       # Settings & env loading
│   │   │   ├── database.py     # SQLite models & connection
│   │   │   ├── routers/
│   │   │   │   ├── projects.py # CRUD for managed repos
│   │   │   │   ├── runs.py     # Run history & status
│   │   │   │   ├── logs.py     # Log streaming & search
│   │   │   │   ├── config.py   # Agent configuration
│   │   │   │   └── system.py   # VM health, systemd status
│   │   │   ├── services/
│   │   │   │   ├── agent.py    # Agent control (start/stop/status)
│   │   │   │   ├── github.py   # GitHub API integration
│   │   │   │   ├── logs.py     # Log file parsing & streaming
│   │   │   │   └── systemd.py  # systemd unit management
│   │   │   └── models.py       # Pydantic schemas
│   │   ├── requirements.txt
│   │   └── alembic/            # DB migrations (if needed)
│   │
│   └── frontend/               # Svelte SPA (compiled to static)
│       ├── src/
│       │   ├── App.svelte
│       │   ├── pages/
│       │   │   ├── Dashboard.svelte    # Overview: runs, costs, status
│       │   │   ├── Projects.svelte     # Manage repos & modes
│       │   │   ├── RunDetail.svelte    # Single run: logs, diff, verdict
│       │   │   ├── Logs.svelte         # Live log viewer
│       │   │   ├── Config.svelte       # Edit agent configuration
│       │   │   └── System.svelte       # VM health, services
│       │   ├── components/
│       │   │   ├── RunCard.svelte      # Run summary card
│       │   │   ├── VerdictBadge.svelte # APPROVE/PR/REJECT badge
│       │   │   ├── LogStream.svelte    # WebSocket log viewer
│       │   │   ├── CostChart.svelte    # Cost over time chart
│       │   │   └── ProjectForm.svelte  # Add/edit project form
│       │   └── lib/
│       │       ├── api.ts              # API client
│       │       └── websocket.ts        # WebSocket helpers
│       ├── package.json
│       ├── vite.config.ts
│       └── index.html
│
├── install.sh                  # One-command installer
├── ARCHITECTURE.md             # This file
├── CLAUDE.md                   # Project conventions
└── README.md
```

---

## Component Details

### 1. Agent Core (`agent/`)

**Extracted from**: `claude-user-memory/.claude/autonomous/`

The agent core is the existing manager/employee system, restructured into a cleaner layout. Changes from the original:

- **Config source**: Instead of reading `manager-config.json` directly, `run-manager.sh` reads from the SQLite database via a helper script (or falls back to JSON file if dashboard is unavailable)
- **Run logging**: Writes structured run records that the dashboard can ingest
- **Webhook notifications**: Posts run events to the dashboard's `/api/webhook/run-event` endpoint for real-time updates

**No changes to core logic** — the manager/employee/analyst prompts and orchestration stay the same.

### 2. Dashboard Backend (`dashboard/backend/`)

**Tech**: Python 3.11+ / FastAPI / SQLite / uvicorn

**Why FastAPI**:
- Python is already on the VM (required by agent scripts)
- Async support for WebSocket log streaming
- Auto-generated OpenAPI docs
- Lightweight, no heavy framework overhead

**Key endpoints**:

```
GET  /                          # Serve frontend SPA
GET  /api/health                # Health check

# Projects (repos the agent manages)
GET  /api/projects              # List all projects
POST /api/projects              # Add a project
PUT  /api/projects/{id}         # Update project config
DEL  /api/projects/{id}         # Remove a project

# Runs (execution history)
GET  /api/runs                  # List runs (filterable)
GET  /api/runs/{id}             # Run detail (logs, diff, verdict)
GET  /api/runs/latest           # Most recent run per project
POST /api/runs/trigger          # Manually trigger a run

# Logs
GET  /api/logs/stream           # WebSocket: live log tail
GET  /api/logs/search           # Search across log files
GET  /api/logs/{run_id}         # Logs for specific run

# Configuration
GET  /api/config                # Current agent config
PUT  /api/config                # Update agent config
GET  /api/config/models         # Available model options

# System
GET  /api/system/status         # systemd service/timer status
GET  /api/system/health         # CPU, memory, disk
POST /api/system/service/{action}  # start/stop/restart timer
GET  /api/system/auth           # Claude auth status
```

**Database schema** (SQLite):

```sql
-- Projects (replaces manager-config.json projects array)
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    repo        TEXT NOT NULL UNIQUE,   -- "owner/repo"
    priority    TEXT DEFAULT 'medium',  -- high/medium/low
    mode        TEXT DEFAULT 'full',    -- full/analyze
    enabled     BOOLEAN DEFAULT 1,
    branch      TEXT DEFAULT 'main',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Runs (parsed from log files + stream data)
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT NOT NULL UNIQUE,   -- "20260308T150314Z"
    project_id  INTEGER REFERENCES projects(id),
    mode        TEXT,                   -- full/analyze
    model       TEXT,                   -- model used
    status      TEXT,                   -- running/success/failed/rejected
    verdict     TEXT,                   -- APPROVE/PR/REJECT/null
    issue_number INTEGER,
    branch      TEXT,
    cost_usd    REAL,
    turns       INTEGER,
    started_at  DATETIME,
    finished_at DATETIME,
    employee_report JSON,              -- full report JSON
    verdict_detail JSON,               -- verdict JSON
    log_file    TEXT                    -- path to .stream.jsonl
);

-- Config (key-value, replaces parts of manager-config.json)
CREATE TABLE config (
    key         TEXT PRIMARY KEY,
    value       TEXT,                   -- JSON-encoded value
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Notifications
CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT REFERENCES runs(run_id),
    type        TEXT,                   -- approve/reject/pr/error/info
    message     TEXT,
    read        BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3. Dashboard Frontend (`dashboard/frontend/`)

**Tech**: Svelte 5 + Vite + TailwindCSS

**Why Svelte**:
- Compiles to vanilla JS (tiny bundle, fast on VM)
- No runtime framework overhead
- Simple, readable components
- Great for small-to-medium dashboards

**Pages**:

| Page | Purpose |
|------|---------|
| **Dashboard** | Overview: active projects, recent runs, cost summary, system health |
| **Projects** | Add/remove/configure repos. Toggle mode (full/analyze), priority, enabled |
| **Run Detail** | View a specific run: employee report, git diff, manager verdict, full logs |
| **Logs** | Live WebSocket log stream + historical search |
| **Config** | Edit global settings: models, limits, schedule, notifications |
| **System** | VM health (CPU/mem/disk), systemd status, auth status, circuit breaker |

**Key UX features**:
- Real-time run status via WebSocket
- Syntax-highlighted git diffs
- Collapsible log sections (thinking vs tool calls vs results)
- Cost tracking charts (daily/weekly)
- One-click "trigger run now" button
- Mobile-responsive (check from phone)

### 4. Installation (`install.sh`)

One-command setup for Rocky Linux 9 (or similar RHEL-based):

```bash
curl -sSL https://raw.githubusercontent.com/kenhaesler/claude-agent-station/main/install.sh | sudo bash
```

**What it does**:
1. Install system dependencies (python3, pip, git, jq, bubblewrap, socat)
2. Install Claude Code CLI
3. Create `claude-agent` system user
4. Copy agent prompts, scripts, configs
5. Set up Python venv + install FastAPI dependencies
6. Initialize SQLite database
7. Install systemd units (agent timer + dashboard service)
8. Configure SELinux policy
9. Configure firewall (allow dashboard port, HTTPS egress)
10. Print access URL and first-run instructions

**Dashboard systemd unit** (new):
```ini
[Unit]
Description=Claude Agent Station Dashboard
After=network.target

[Service]
Type=simple
User=claude-agent
WorkingDirectory=/opt/claude-agent-station/dashboard/backend
ExecStart=/opt/claude-agent-station/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8420
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Data Flow

### Scheduled Run (existing flow, enhanced)

```
systemd timer fires (hourly)
    │
    ▼
run-manager.sh
    │
    ├─→ POST /api/webhook/run-event {status: "started", project: "..."}
    │   (dashboard records run start)
    │
    ├─→ Spawn employee agent → works on issues
    │   └─→ Writes .stream.jsonl (dashboard tails via inotify)
    │
    ├─→ Collect report, spawn manager review
    │   └─→ Manager writes verdicts.json
    │
    ├─→ Execute verdicts (push/merge/PR/reject)
    │
    └─→ POST /api/webhook/run-event {status: "completed", verdict: "...", cost: ...}
        (dashboard records run completion)
```

### Manual Trigger (new)

```
User clicks "Run Now" in dashboard
    │
    ▼
POST /api/runs/trigger {project_id: 1}
    │
    ▼
Backend: systemctl start claude-agent.service
    │
    ▼
(same flow as scheduled run)
```

### Live Log Viewing (new)

```
User opens Logs page
    │
    ▼
WebSocket: /api/logs/stream?run_id=latest
    │
    ▼
Backend: tail -f /var/log/claude-agent/*.stream.jsonl
    │
    ▼
Parse JSONL → send structured events to frontend
    │
    ▼
Frontend renders: thinking blocks, tool calls, results
```

---

## Migration Plan (from claude-user-memory)

### Phase 1: Extract & Restructure
- Copy autonomous files to new repo structure
- Refactor `run-manager.sh` to use new paths
- Add webhook calls for dashboard integration
- Keep JSON config as fallback (dashboard optional)

### Phase 2: Dashboard Backend
- FastAPI app with SQLite
- REST API for projects, runs, config
- WebSocket log streaming
- systemd control endpoints
- Import existing log files into database

### Phase 3: Dashboard Frontend
- Svelte SPA with TailwindCSS
- Dashboard overview page
- Project management
- Run detail viewer
- Live log streaming

### Phase 4: Installer & Docs
- One-command install script
- Migration guide from claude-user-memory
- README with screenshots

---

## Security Considerations

- **Dashboard auth**: HTTP Basic Auth or token-based (configurable). No auth by default on localhost-only binding.
- **Bind address**: Default `127.0.0.1:8420` (localhost only). Reverse proxy (nginx/caddy) for external access with TLS.
- **systemd control**: Only start/stop/restart of the agent timer. No arbitrary command execution.
- **Config validation**: All config changes validated before writing. No path traversal, no arbitrary file access.
- **Log access**: Only reads from `/var/log/claude-agent/`. No access to other system logs.
- **GH_TOKEN**: Never exposed via API. Dashboard shows auth status only (valid/expired/missing).

---

## Tech Stack Summary

| Component | Technology | Reason |
|-----------|-----------|--------|
| Agent orchestration | Bash + Claude CLI | Existing, proven, works |
| Backend API | Python/FastAPI | Already on VM, async, lightweight |
| Database | SQLite | Zero config, single file, sufficient for this scale |
| Frontend | Svelte 5 + Vite | Tiny bundle, no runtime, fast |
| Styling | TailwindCSS | Utility-first, no custom CSS files |
| Process management | systemd | Already in use, native to Rocky Linux |
| Auth (optional) | HTTP Basic / Token | Simple, sufficient for single-user VM |

---

## Future Ideas (not in v1)

- **Multi-VM support**: Central dashboard managing agents on multiple VMs
- **Slack/Discord notifications**: Beyond file-based notifications
- **Cost budgeting**: Set monthly limits with automatic pause
- **Run scheduling UI**: Cron expression editor in the dashboard
- **Issue queue view**: See what the agent will work on next
- **Diff review in UI**: Approve/reject verdicts from the dashboard
- **Agent log replay**: Step-through visualization of agent thinking
