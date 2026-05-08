# Concepts

*The mental model behind Claude Agent Station. Read this once before tweaking anything.*

## Agent Teams

Three roles, each running a different model:

| Role | Default model | Responsibility |
|------|---------------|----------------|
| Lead | `claude-sonnet-4-6` | Fetches eligible issues, decomposes them into tasks, spawns exactly three role-specialized teammates (`backend`, `frontend`, `qa`), reviews plans for conflicts, monitors until all work completes |
| Teammates | `claude-opus-4-7` | Three role specialists per run; each works in its own git worktree on the tasks routed to its specialty — reads code, plans, implements, tests, commits locally |
| Manager | `claude-sonnet-4-6` | Reviews all teammate work post-completion and issues a verdict |

The lead and teammates run inside a single Claude Agent SDK Agent Teams session driven by `agent/station_orchestrator.py`. The manager is a separate review pass invoked by `agent/scripts/run-manager.sh` after teammates finish.

## Verdicts

After the manager reviews completed work, every run terminates with one verdict:

| Verdict | What happens to the work |
|---------|--------------------------|
| `APPROVE` | Branch is pushed and merged into `dev` |
| `PR`      | Pull request is opened against `dev` for human review |
| `REJECT`  | Branch is discarded, run marked failed |
| `SKIP`    | Manager declined to act — no eligible work for this project; queue item marked completed (not failed), no branch changes |

## Issue lifecycle

The lead picks issues that pass these filters:
- Repository is enabled in the dashboard.
- Issue is open.
- Issue has no label in the skip set. The full skip set (from `agent/station_orchestrator.py` `SKIP_LABELS`) is: `autonomous-agent/in-progress`, `autonomous-agent/needs-help`, `NO AI`, `backlog`, `wontfix`, `vision-suggested`. Issues labeled `backlog` are never picked up.

Eligible issues are then decomposed into tasks and distributed across the three teammates by specialty. A single issue may produce work for multiple teammates (e.g. a backend change plus a frontend update plus QA coverage), and multiple issues feed the same three teammates within one run. Each teammate works inside its own git worktree under `/home/claude-agent/workspaces/` so concurrent teammates do not collide.

## Plans

Every teammate writes a short implementation plan as its first deliverable. The lead reviews each plan before the teammate is allowed to start implementation. The lead can:
- Approve the plan and let work proceed.
- Reject the plan if it conflicts with another teammate's work or with the project vision; the teammate revises and resubmits.

This sequencing prevents two teammates from racing into incompatible changes.

## Plan-usage throttling

Claude usage is bounded by the active plan tier. The system tracks weekly token consumption in the `plan_usage_history` table and exposes a throttle decision via the dashboard API. When weekly usage (or any single model's usage) crosses the configured threshold, `run-manager.sh` short-circuits before launching a new run rather than starting work it cannot finish. Independent of throttling, every Claude invocation passes a `--fallback-model` to the SDK so primary-model errors don't kill the run: Opus 4.7 falls back to Sonnet 4.6, and Sonnet 4.6 falls back to Haiku 4.5. The dashboard surfaces current usage and the active throttle state on the Command Center page.

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

Each tool call produces exactly one row, written in two phases: the pre-tool hook does `INSERT OR IGNORE` keyed by `idempotency_key` (the SDK's `tool_use_id`) with `status='started'`; the post-tool hook then `UPDATE`s that same row with the final status, exit code, output tails, and `finished_at`. The unique `idempotency_key` makes both phases retry-safe — a re-fired pre-tool hook is ignored, and the row is finalized only when the tool completes. Rows are never deleted (a background retention task trims old rows by age, default 30 days). Use the audit log to reconstruct what a teammate did and when.

## Worktrees and isolation

Each teammate runs in its own git worktree at `<workspaces_dir>/<repo-name>-<role>` (e.g. `/home/claude-agent/workspaces/my-repo-backend`). The three teammate roles are `backend`, `frontend`, and `qa`. Worktrees are created per run and torn down when the run ends. This isolation means file edits from one teammate are invisible to others until they are committed and merged.
