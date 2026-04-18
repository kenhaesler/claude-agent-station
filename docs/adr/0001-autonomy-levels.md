# ADR-0001: Three-Level Autonomy Model for Agent Runs

- **Status:** Accepted (2026-04-18)
- **Deciders:** Claude Agent Station maintainer
- **Related:** Phase 1 of the Auto Mode rollout plan (`/root/.claude/plans/fluttering-meandering-clarke.md`)

## Context

Today the Lead agent in `agent/station_orchestrator.py:696` constructs `ClaudeAgentOptions` with **none** of the SDK's permission controls: no `permission_mode`, no `can_use_tool`, no `hooks`, no `max_budget_usd`. Teammates spawned through the Agent SDK already declare `permissionMode: bypassPermissions` in `agent/agents/issue-worker.md:6`, so in practice the station already runs at full autonomy — but without an audit trail, without per-tool policy, and without a dashboard surface an operator can use to intervene.

Claude Code ships **Auto Mode** as a first-class concept: continuous, autonomous execution with reasonable assumptions and minimal interruptions. This ADR formalises the same concept inside Claude Agent Station so that:

- The operator chooses the autonomy level explicitly, per project and per run.
- Every tool invocation flows through a named policy with an audit row.
- Destructive actions still require humans by default.
- Running "Auto" in this station looks and feels like running Claude Code in Auto Mode, not like running the existing de-facto bypass with more tests.

## Decision

Introduce three autonomy levels — `manual`, `assisted`, `auto` — persisted per `Project` (default) and per `Run` (override). Each level maps to a distinct SDK configuration and a row of the policy matrix. Existing projects backfill to `assisted`, preserving today's operator-visible behaviour (teammates still run with their `bypassPermissions` frontmatter; only Lead-agent tool choice is now gated).

### SDK mapping

| Level      | `permission_mode`     | `can_use_tool` posture                         | `max_budget_usd`         | Notes                                                                 |
|------------|-----------------------|------------------------------------------------|--------------------------|-----------------------------------------------------------------------|
| `manual`   | `'default'`           | Defers non-read tools to the permission tray   | Project-level (required) | Write/Edit/destructive Bash require tray approval                     |
| `assisted` | `'acceptEdits'`       | Allows Edit/Write; defers destructive Bash     | Project-level or null    | Status quo for Lead; policy engine still runs and audits              |
| `auto`     | `'bypassPermissions'` | Allows edits + safe + destructive within deny-list | Required (hard ceiling) | Matches Claude Code Auto Mode; draft PR auto-open allowed when both project AND run are `auto` |

### Policy matrix

| Category                      | Examples                                | `manual`  | `assisted` | `auto` |
|------------------------------|-----------------------------------------|-----------|------------|--------|
| Read-only file tools         | `Read`, `Glob`, `Grep`                  | Allow     | Allow      | Allow  |
| Read-only Bash               | `ls`, `cat`, `git status`, `git diff`   | Allow     | Allow      | Allow  |
| Edit tools                   | `Edit`, `Write`                         | Defer     | Allow      | Allow  |
| Destructive Bash             | `rm -rf`, `chmod -R 777`, `git reset --hard`, `sudo` | Defer | Defer | Allow  |
| Subagent spawn               | `Agent`                                 | Allow     | Allow      | Allow  |
| Always-deny — critical       | `git push … main`, `git push --force`, `rm -rf /`, `DROP TABLE/DATABASE`, service stop/restart, secret echo | Deny | Deny | Deny |
| Unknown tool                 | Anything not in the table above         | Deny      | Deny       | Deny   |

"Defer" means the policy engine returns an `await-tray` decision: the agent pauses, a row is inserted into `permission_requests`, and the SSE bus notifies the dashboard tray (built in Phase 2). Default timeout: 5 minutes → auto-deny.

### Human gates (always manual, regardless of level)

- PR merge to `main` — enforced at the GitHub branch-protection layer, not at the SDK.
- `git push --force` on any shared branch.
- Service control on `claude-agent.service` / `claude-station-dashboard.service`.
- Direct writes to `/var/lib/claude-agent-station/station.db` outside the FastAPI router layer.
- Reading secret files (`.env`, `credentials.json`, `~/.config/gh/hosts.yml`, any path matching `AWS_SECRET`/`ANTHROPIC_API_KEY`/`GITHUB_TOKEN` env patterns).

These are not opt-outable via `auto`. The policy engine's `ALWAYS_DENY` list enforces them before any per-level check runs.

### Storage

- `projects.autonomy_level TEXT DEFAULT 'assisted'`, `projects.max_budget_usd REAL`.
- `runs.autonomy_level TEXT DEFAULT 'assisted'` (snapshot at trigger time), `runs.max_budget_usd REAL`.
- Migrations via the existing `_migrate_add_columns` pattern in `dashboard/backend/app/database.py` — no Alembic.

### Audit

Every policy decision (allow, deny, defer) writes one row to `agent_events` with `event_type='auto_mode_decision'` and `event_data` containing `{tool, input_summary, decision, reason, level}`. Secrets are redacted from `input_summary` at the hook boundary. The existing `agent_events` table (`models.py:221-233`) already has the right shape — no schema change needed.

## Consequences

### Positive

- **Auditability.** The current de-facto `bypassPermissions` silently allows everything; the new model records every decision with reason.
- **Graceful degradation.** `assisted` (the default) preserves today's operator experience while the policy engine silently audits — no UX regression on the day the code lands.
- **Clear escape hatch.** An operator can dial a specific risky project down to `manual` and keep every other project at `auto` without code changes.
- **Stronger guarantees under `auto`.** The `ALWAYS_DENY` list catches push-to-main / force-push / drop-table / secret echo even when the rest of the model says "just do it."

### Negative

- **SDK coupling.** Our policy layer depends on `ClaudeAgentOptions.{permission_mode, can_use_tool, hooks, max_budget_usd}`. Pinned at `claude-agent-sdk==0.1.50` (P1.T1); bumping requires re-verifying hook signatures.
- **Logging overhead.** Every tool call writes one `agent_events` row. Estimated overhead ≤ 2% of run wall time at current run sizes; will be re-evaluated if any single run exceeds ~5k tool calls.
- **Policy false-positives.** A legitimate `rm -rf node_modules` at `assisted` will defer to the tray until explicitly allowed. Mitigation: operator can start a new run at `auto` for that task.
- **Migration is forward-only.** SQLite `ALTER TABLE DROP COLUMN` is not used here. Rollback is "feature-flag off, leave the column."

## Alternatives Considered

1. **Two-level model (`manual` / `auto`).** Rejected because it forfeits the safe default of `assisted` (today's behaviour) and forces every project to pick a side on day one.
2. **Four-level model with `supervised`.** Rejected as scope creep; the permission tray at `manual` already delivers the "human approves each write" experience.
3. **Policy as hook-only (no `can_use_tool`).** Rejected because hooks fire after the SDK has made its decision; `can_use_tool` is where a deny can actually stop the call.
4. **Level stored only on `Run`, inherited every trigger.** Rejected because the operator wants a per-project default so repos don't need to be re-configured on every run.

## Adoption Plan

1. **P1.T3** — ship migrations with `assisted` default.
2. **P1.T4** — land the policy engine behind a feature flag (no caller yet).
3. **P1.T5** — wire `ClaudeAgentOptions` in `station_orchestrator.py`. Behaviour unchanged for existing `Run`s.
4. **P1.T6** — audit hook writes rows; zero operator-visible changes.
5. **P1.T7** — prototype run documents the three levels against a throwaway repo; baseline numbers become Phase 2 regression thresholds.
6. **Phase 2** — UI toggle + permission tray + autonomy badge + CI gate.
7. **Phase 3** — audit dashboard + analytics.

## Open Questions

- Permission-tray timeout of 5 minutes: should this be project-configurable, or is a global default sufficient? Punted to Phase 2.
- Per-project `max_budget_usd` as a cap on per-run overrides vs as a default: current plan treats it as a default; revisit if operators ask for a cap.
- Whether `CAS_POLICY_BYPASS=1` env escape-hatch belongs here (e.g. for local dev). Left out of the first cut; revisit if teammates need it.
