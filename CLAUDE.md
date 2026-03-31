# Claude Agent Station

## Project Overview
Self-hosted autonomous Claude Code agent with web dashboard. Uses **Agent Teams mode** (Claude Agent SDK) for multi-project GitHub automation. A lead agent coordinates teammates that each work on a single issue in isolated worktrees.

## Tech Stack
- **Agent Core**: Claude Agent SDK (Python) + bash run-manager
- **Lead Agent**: Sonnet 4.6 — coordinates teammates, reviews plans, monitors progress
- **Teammates**: Opus 4.6 — implement issues via `issue-worker` agent definition
- **Backend**: Python 3.11+ / FastAPI / SQLite / uvicorn
- **Frontend**: Svelte 5 + Vite + TailwindCSS
- **Deployment**: systemd (Rocky Linux 9 / RHEL-based)

## Architecture
See `ARCHITECTURE.md` for full system design.

**Agent Teams flow**: run-manager.sh → station_orchestrator.py → Claude Agent SDK → Lead spawns teammates → each teammate works one issue → Lead reviews → Manager reviews all work → verdicts (APPROVE/PR/REJECT)

## Conventions
- Backend code in `dashboard/backend/app/`
- Frontend code in `dashboard/frontend/src/`
- Agent definitions in `agent/agents/` (Agent Teams worker definitions)
- Agent prompts in `agent/prompts/` (manager review prompt)
- Agent scripts in `agent/scripts/`
- Orchestrator: `agent/station_orchestrator.py`
- All Python code uses type hints
- FastAPI routers in separate files per domain
- Svelte components use TypeScript
- SQLite database at `/var/lib/claude-agent-station/station.db`
- Dashboard port: 8420

## Issue Rules
- **NEVER work on issues or features labeled `backlog`.** Under no circumstances should the agent pick up, implement, plan, or research any issue/feature that carries the `backlog` label. Skip them entirely — no exceptions.

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
