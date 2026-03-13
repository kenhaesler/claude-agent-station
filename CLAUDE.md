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

## Git Workflow
- **`main` is protected.** Direct pushes are blocked — even for admins.
- All changes go through pull requests (no approval required — solo project).
- Force pushes and branch deletion on `main` are blocked.
- **Workflow**: Create a feature branch → commit changes → open PR via `gh pr create` → merge.
- Branch naming: `feature/<description>`, `fix/<description>`, or `autonomous/issue-<number>`.
- Never commit `.env`, credentials, or binary files.

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
