# Claude Agent Station

## Project Overview
Self-hosted autonomous Claude Code agent with web dashboard. Manager/Employee architecture for multi-project GitHub automation.

## Tech Stack
- **Agent Core**: Bash scripts + Claude CLI (manager/employee/analyst prompts)
- **Backend**: Python 3.11+ / FastAPI / SQLite / uvicorn
- **Frontend**: Svelte 5 + Vite + TailwindCSS
- **Deployment**: systemd (Rocky Linux 9 / RHEL-based)

## Architecture
See `ARCHITECTURE.md` for full system design.

## Conventions
- Backend code in `dashboard/backend/app/`
- Frontend code in `dashboard/frontend/src/`
- Agent prompts in `agent/prompts/`
- Agent scripts in `agent/scripts/`
- All Python code uses type hints
- FastAPI routers in separate files per domain
- Svelte components use TypeScript
- SQLite database at `/var/lib/claude-agent-station/station.db`
- Dashboard port: 8420

## Development
```bash
# Backend
cd dashboard/backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8420

# Frontend
cd dashboard/frontend && npm install && npm run dev
```

## Key Paths (deployed)
- Config DB: `/var/lib/claude-agent-station/station.db`
- Agent logs: `/var/log/claude-agent/`
- Workspaces: `/home/claude-agent/workspaces/`
- Agent scripts: `/opt/claude-agent-station/agent/`
- Dashboard: `/opt/claude-agent-station/dashboard/`
