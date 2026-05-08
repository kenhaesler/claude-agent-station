# Concepts

*The mental model behind Claude Agent Station. Read this once before tweaking anything.*

## Agent Teams

Three roles, each running a different model:

| Role | Default model | Responsibility |
|------|---------------|----------------|
| Lead | `claude-sonnet-4-6` | Fetches eligible issues, spawns one teammate per issue, reviews plans for conflicts, monitors until all work completes |
| Teammates | `claude-opus-4-7` | Each works on a single GitHub issue in an isolated git worktree — reads code, plans, implements, tests, commits locally |
| Manager | `claude-sonnet-4-6` | Reviews all teammate work post-completion and issues a verdict |

The lead and teammates run inside a single Claude Agent SDK Agent Teams session driven by `agent/station_orchestrator.py`. The manager is a separate review pass invoked by `agent/scripts/run-manager.sh` after teammates finish.

## Verdicts

After the manager reviews completed work, every run terminates with one verdict:

| Verdict | What happens to the work |
|---------|--------------------------|
| `APPROVE` | Branch is pushed and merged into `dev` |
| `PR`      | Pull request is opened against `dev` for human review |
| `REJECT`  | Branch is discarded, run marked failed |

## Issue lifecycle

The lead picks issues that pass these filters:
- Repository is enabled in the dashboard.
- Issue is open.
- Issue has no label in the skip set. The full skip set (from `agent/station_orchestrator.py`) is: `autonomous-agent/in-progress`, `autonomous-agent/needs-help`, `NO AI`, `backlog`, `wontfix`, `vision-suggested`. **Issues labeled `backlog` are skipped without exception** — see `CLAUDE.md`.
- The analyst applies `autonomous-agent/refined` to issues it has already analyzed; that label is also in the skip set, so the lead does not re-assign already-refined issues.

Each picked issue is handed to its own teammate. Teammates work inside dedicated git worktrees under `/home/claude-agent/workspaces/` so concurrent teammates do not collide.

## Plans

Every teammate writes a short implementation plan as its first deliverable. The lead reviews each plan before the teammate is allowed to start implementation. The lead can:
- Approve the plan and let work proceed.
- Reject the plan if it conflicts with another teammate's work or with the project vision; the teammate revises and resubmits.

This sequencing prevents two teammates from racing into incompatible changes.

## Plan-usage throttling

Claude usage is bounded by the active plan tier. The system tracks weekly token consumption in the `plan_usage_history` table. When usage approaches the tier limit, the orchestrator can fall back to a smaller model for non-critical work. The dashboard surfaces current usage and the active throttle state on the Command Center page.

## Audit log

The `audit_log` table records every action taken by an agent. Key fields:

| Field | Meaning |
|-------|---------|
| `actor` | Which agent ran the tool: `"lead"`, `"teammate-<agent_id>"`, or `"manager"` |
| `action_kind` | Tool category, e.g. `"tool.bash"`, `"tool.edit"` |
| `action_detail` | JSON payload: command, file path, etc. |
| `status` | `"started"` → `"ok"` / `"error"` / `"timeout"` |
| `run_id` | Links the row to the workflow run |
| `started_at` / `finished_at` | Wall-clock timing for the tool call |

The log is append-only: rows are never updated or deleted (the pre-tool hook inserts a `status='started'` row; the post-tool hook fills in the result fields on the same row via the unique `idempotency_key`). Use the audit log to reconstruct what a teammate did and when.

## Worktrees and isolation

Each teammate runs in its own git worktree at `<workspaces_dir>/<repo-name>-<role>` (e.g. `/home/claude-agent/workspaces/my-repo-backend`). The three teammate roles are `backend`, `frontend`, and `qa`. Worktrees are created per run and torn down when the run ends. This isolation means file edits from one teammate are invisible to others until they are committed and merged.
