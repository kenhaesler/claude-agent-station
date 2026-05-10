> **TL;DR** — Lead coordinates, three teammates implement, manager reviews. Each runs a different Claude model.

The station's work is split across three roles. Each role has a different responsibility, prompt, and model:

| Role | Model | Responsibility |
|---|---|---|
| **Lead** | Sonnet 4.6 | Picks eligible issues, decomposes them into tasks, spawns the three teammates, reviews their plans, and watches the run until done. |
| **Teammates** (×3) | Opus 4.7 | Three role-specialists per run — `backend`, `frontend`, `qa`. Each works in its own git worktree on the tasks routed to its specialty. |
| **Manager** | Sonnet 4.6 | A separate review pass after teammates finish. Reads the work and issues a verdict (APPROVE / PR / REJECT / SKIP). |

The lead and teammates run inside a single Claude Agent SDK Agent Teams session. The manager runs as a follow-up phase.

<!-- under-the-hood -->

- Agent definitions: `agent/agents/issue-worker.md` (teammate worker definition).
- Prompts: `agent/prompts/manager.md`, role overlays in `agent/prompts/roles/`.
- Orchestrator that spawns lead + teammates: `agent/station_orchestrator.py`.
- Manager invocation: `agent/scripts/run-manager.sh`.
- Per-role model overrides live in the agent config DB (`/var/lib/claude-agent-station/station.db`).
