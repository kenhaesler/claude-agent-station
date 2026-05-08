# Docs Overhaul — Design

**Date:** 2026-05-08
**Status:** Draft, awaiting user review
**Audience for the resulting docs:** Self-hosters cloning the repo onto their own VM, plus the project maintainer using the docs as a personal runbook.

## Problem

The repo's user-facing documentation has drifted against the codebase since the April 2026 Agent Teams rebuild, and there is no operator-facing material at all.

Concrete drift identified against the live tree:

- `README.md` line 32 says teammates run **Opus 4.6**; current default is **Opus 4.7** (commit `4eeda11`, `CLAUDE.md`).
- `README.md` dashboard section lists pages that don't match the actual `dashboard/frontend/src/pages/` (`CommandCenterPage`, `WorkStreamPage`, `DecisionsPage`, `ConfigPage`).
- `ARCHITECTURE.md` `agent/` tree omits real files (`audit_hook.py`, `auto_mode.py`, `launcher.py`, `run_control.py`, `tray_referral.py`, `vision.py`, `vision_analyst.py`, `vision_scoring.py`, `skills/`).
- `ARCHITECTURE.md` prompts directory lists only `manager.md` + `custom/`; the directory actually contains 12 files (analyst, assigner, employee, manager, planner, REPORT-SCHEMAS, reviewer, security-reviewer, triager, vision_create, vision_refine, plus a `roles/` subdir).
- `ARCHITECTURE.md` database schema lists 14 tables but does not mention the `audit_log` table added in commit `c02cc8c`.
- Every count in `ARCHITECTURE.md` ("20 API routers", "14 tables", "21 test files / 325+ tests", "4 page components", "38 components") needs to be re-derived.
- The `routers/coordinator.py` line in the directory tree may be stale — verify against the live `routers/` directory before keeping it.
- There is no install guide, configuration reference, or operations runbook anywhere.

## Goals

1. Restore factual accuracy of `README.md` and the architecture document.
2. Add user/operator documentation covering install, configuration, operations, and concepts — written for a stranger cloning the repo, which incidentally serves the maintainer too.
3. Keep documentation surface small and zero-build (plain markdown rendered by GitHub).
4. Make ongoing accuracy maintenance cheap (a single `CLAUDE.md` rule, no CI doc-lint, no static-site generator).

## Non-goals

- Screenshots of the dashboard. UI is iterating fast; revisit after it stabilizes.
- Versioned docs, changelog page, MkDocs / Material, GitHub Pages, or any other static-site generator.
- A user-facing migration-history page (`migrations/` is internal).
- A contributor / developer guide. `CLAUDE.md` and the architecture document already cover that audience.
- Any change to dashboard or agent code. This is a documentation-only effort.

## Final layout

```
README.md                    # front door — pitch, quick start, links
CLAUDE.md                    # agent conventions (unchanged in scope, one link patched)
ARCHITECTURE.md              # 3-line stub redirecting to docs/architecture.md
docs/
├── architecture.md          # moved from root, accuracy-patched
├── install.md               # new
├── configuration.md         # new
├── operations.md            # new
├── concepts.md              # new
├── adr/                     # unchanged
├── prototypes/              # unchanged
└── superpowers/             # unchanged
```

The root `ARCHITECTURE.md` becomes a stub (title + one-line redirect to `docs/architecture.md`) so external bookmarks, prior PR descriptions, and prior issue comments do not 404.

## Per-document content

### `README.md` (rewrite, ~80–100 lines)

Sections in order:

1. Title + CI badge (existing).
2. One-line pitch.
3. "What it does" — current paragraph, lightly trimmed.
4. Architecture diagram — keep the existing ASCII block. Fix the model labels (Opus 4.7).
5. Quick start — three commands max, plus the dashboard URL. No expanded prerequisite list (that lives in `docs/install.md`).
6. Documentation nav table — five rows, one per doc under `docs/`:
   - Install guide → `docs/install.md`
   - Configuration → `docs/configuration.md`
   - Operations → `docs/operations.md`
   - Concepts → `docs/concepts.md`
   - Architecture → `docs/architecture.md`
7. Origin (extracted-from blurb).
8. License.

Removed from the current README: full Python dependency-management section (moves to `docs/install.md`), the project-config JSON example (moves to `docs/configuration.md`), the verbose dashboard feature list (folds into `docs/concepts.md`).

### `docs/install.md`

Header: "How to deploy Claude Agent Station on a fresh VM. Audience: anyone setting up their own instance."

Sections:

1. **Prerequisites** — Rocky Linux 9 / RHEL-based, Python 3.11+, Claude Code CLI (with valid auth), GitHub token with repo access, systemd, git, jq.
2. **Automated install** — `sudo bash install.sh`. Brief description of what it does (creates `claude-agent` user, installs Python deps from the lock file, sets up systemd units, applies SELinux policy, starts the dashboard).
3. **Manual install** — high-level mirror of `install.sh`: create user, clone, create venv, `pip install -r requirements-lock.txt`, install systemd units, enable + start dashboard service, enable agent timer.
4. **First-run walkthrough** — open dashboard, run Claude OAuth flow, paste GitHub token, add the first project (label conventions, `backlog` exclusion), wait for the timer.
5. **Updating an existing install** — pull, dependency drift check, restart units.
6. **Updating Python dependencies** — the `pip-compile` flow currently inlined in `README.md`.
7. **Uninstalling** — stop units, remove user, remove `/var/lib/claude-agent-station/`.

### `docs/configuration.md`

Header: "Reference for every configurable setting. Audience: operators tuning the system."

Sections:

1. **Where config lives** — `station.db` `config` table is canonical; JSON at `STATION_CONFIG_PATH` is the synced view; sync direction.
2. **Environment variables** — full table, derived from `dashboard/backend/app/config.py`. Columns: variable, default, description, example.
3. **Models** — Lead / Teammates / Manager defaults (Sonnet 4.6 / Opus 4.7 / Sonnet 4.6, verified against `agent/config/` and `station_orchestrator.py`). How to override per role.
4. **Budgets and rate limits** — token caps, plan-tier throttling pointer to concepts.
5. **Schedule** — systemd timer cadence, where to change it.
6. **Autonomy levels** — short pointer to `docs/adr/0001-autonomy-levels.md`.
7. **API key and webhook secret** — `STATION_API_KEY`, `STATION_WEBHOOK_SECRET`; which endpoints are public, which require auth.
8. **Project config** — repo, priority, mode, enabled, branch (the JSON block currently in README, expanded with field descriptions).

### `docs/operations.md`

Header: "When something is wrong or you need to act on the running system. Audience: operators."

Sections, ordered by likely frequency of need:

1. **Service control** — `systemctl status/start/stop/restart claude-station-dashboard` + the agent timer/service names.
2. **Log locations** — `/var/log/claude-agent/` (agent runs), journalctl (dashboard service), what each contains.
3. **Common failures and fixes**:
   - Stuck run / orphan recovery (`stale_run_reaper` service).
   - OAuth token expired (`agent/scripts/refresh-token.py`).
   - Circuit breaker tripped (3-strike rule, where state lives, how to clear).
   - Plan-tier throttle (when 4.7 falls back, how to verify in the dashboard).
   - Dashboard 401 / wrong API key.
4. **Audit trail** — where to read `audit_log`, how to filter by `agent_id`, append-only receipt log location.
5. **Upgrade procedure** — pull, dep drift check, restart.
6. **Disaster recovery** — DB backup/restore, workspace cleanup under `/home/claude-agent/workspaces/`.

### `docs/concepts.md`

Header: "The mental model behind Claude Agent Station. Read this once before tweaking anything."

Sections:

1. **Agent Teams** — Lead / Teammates / Manager: which model each runs, what each is responsible for, when each runs.
2. **Verdicts** — APPROVE (push + merge), PR (open for human review), REJECT (discard branch). What each does to the worktree and the GitHub repo.
3. **Issue lifecycle** — labels (`backlog` excluded by project rule, `autonomous-agent/refined`, `autonomous-agent/analyzed`), eligibility filtering, worktree-per-issue isolation.
4. **Plans** — why teammates write a plan first, how the lead reviews it, what happens on rejection.
5. **Plan-usage throttling** — what happens when weekly token usage hits the plan tier limit, how the system decides to throttle.
6. **Audit log** — what's recorded, why it's append-only, how it's used.
7. **Worktrees and isolation** — how teammate work is sandboxed under `/home/claude-agent/workspaces/`.

### `docs/architecture.md` (moved from root, accuracy-patched)

Same overall structure as today's `ARCHITECTURE.md`. Patches applied:

- Directory tree: list every real file under `agent/`. Verify `agent/coordinator/` — if it is now empty (just `__pycache__` directories), remove it from the tree.
- Prompts: list every real file in `agent/prompts/`.
- Routers: re-list from `dashboard/backend/app/routers/` directly. Drop any router that no longer exists. Add any that are new.
- Database schema table: add the `audit_log` row; verify the existing 14 against `models.py`. Update the leading count.
- Recount everything: routers, tables, page components, components, test files. If a count is hard to derive accurately, omit it rather than invent it.
- Tech-stack table: update test count if it's quoted; otherwise leave alone.
- Other sections (Security, Development, Environment Variables, Deployment Model): keep as-is unless verification surfaces a problem.

### Root `ARCHITECTURE.md` stub

```markdown
# Architecture

This document has moved to [`docs/architecture.md`](docs/architecture.md).
```

Three lines, nothing more.

## Cross-cutting concerns

### Tone

- `README.md`: existing light marketing voice. Keep.
- `docs/install.md`, `docs/operations.md`: imperative, command-first, terse. Use real paths from this repo, never placeholders.
- `docs/configuration.md`: reference style — tables and short prose, skimmable.
- `docs/concepts.md`: explainer prose, plain English, no marketing language. Define each term once and link from other docs.
- `docs/architecture.md`: technical, factual, structural.

### Cross-linking

- Every `docs/*.md` opens with a one-line "what this is / who it's for" header.
- `install.md` links forward to `configuration.md` (after install) and `operations.md` (when it breaks).
- `configuration.md` links to `concepts.md` for terms (verdicts, throttling, autonomy levels).
- `operations.md` links to `architecture.md` for "why this exists" and `concepts.md` for terms.
- `README.md` is the only document with a full nav table; no other doc duplicates that nav.

### Inbound-link patches

Patch every file that currently references `ARCHITECTURE.md` at the repo root to point at `docs/architecture.md` instead. Files known in advance:

- `README.md` (footer link, currently line 108).
- `CLAUDE.md` (the line "See `ARCHITECTURE.md` for full system design.").

Implementation step will `grep -rn 'ARCHITECTURE\.md'` across the tracked tree and patch every hit, excluding the new stub itself.

### Anti-drift verification

Every count, path, model name, env var, table name, prompt name, or router name written into the new docs must be derived from the live repo at write time, not recalled from memory or the prior `ARCHITECTURE.md`. Specific commands:

- File listings: `ls <dir>`.
- Counts: `find <dir> -name '<pattern>' | wc -l`.
- Routers: `ls dashboard/backend/app/routers/`.
- Prompts: `ls agent/prompts/`.
- DB tables: `grep -h __tablename__ dashboard/backend/app/models.py`.
- Env vars: `grep -E '^\s*\w+:' dashboard/backend/app/config.py` and verify against `STATION_` prefix usage.
- Default models: read `agent/config/` defaults and `agent/station_orchestrator.py`.
- Test count: `find dashboard/backend/tests -name 'test_*.py' | wc -l`.

If a value cannot be cleanly derived, omit it rather than invent it.

### Maintenance

Add one rule to `CLAUDE.md` under existing conventions: when changing models, env vars, DB tables, routers, or agent prompts, update the corresponding section in `docs/`. No CI doc-lint, no doc-test framework, no `mkdocs` build.

## Risks

- **Inbound-link breakage outside the repo.** Issue comments and PR descriptions referencing `ARCHITECTURE.md` at root will still resolve thanks to the stub redirect. External blog posts or bookmarks pointing deeper (e.g., to a specific anchor in `ARCHITECTURE.md`) will break — accepted cost.
- **Re-derived counts may surface code-state surprises.** If verification reveals e.g. a router that exists but is unused, that's a code-cleanup signal, not a docs problem; flag it but do not act on it in this scope.
- **Drift will recur.** The `CLAUDE.md` rule is best-effort. If drift becomes a chronic problem, revisit with a CI doc-lint as a separate effort.

## Acceptance criteria

- `README.md` is rewritten per the outline above; all model versions match `CLAUDE.md` and `agent/config/`; the dashboard feature list and project-config JSON are removed.
- `docs/architecture.md` exists; root `ARCHITECTURE.md` is a 3-line stub.
- `docs/install.md`, `docs/configuration.md`, `docs/operations.md`, `docs/concepts.md` exist with the section structure above and content derived from the live repo.
- Every reference to `ARCHITECTURE.md` (other than the stub itself) points at `docs/architecture.md`.
- `CLAUDE.md` has a one-line maintenance rule for keeping docs in sync.
- All counts and lists in `docs/architecture.md` are verified against the live repo at write time.
- No code outside `docs/`, `README.md`, `CLAUDE.md`, and the `ARCHITECTURE.md` stub is changed.
