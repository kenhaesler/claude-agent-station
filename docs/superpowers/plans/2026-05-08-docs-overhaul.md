# Docs Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore factual accuracy of `README.md` and the architecture document, and add four new user/operator docs (install, configuration, operations, concepts) under `docs/`.

**Architecture:** Plain markdown rendered by GitHub. README is a short front door. `ARCHITECTURE.md` moves to `docs/architecture.md` with a 3-line stub left at the root for inbound-link compatibility. Four new audience-specific docs live alongside it. Single CLAUDE.md rule keeps content in sync going forward.

**Tech Stack:** Markdown only. No build tooling.

**Spec:** [`docs/superpowers/specs/2026-05-08-docs-overhaul-design.md`](../specs/2026-05-08-docs-overhaul-design.md)

**Conventions for every prose-writing step:**
- Open every `docs/*.md` (except `architecture.md`) with a one-line italic header `*What this is — who it's for.*` per the spec.
- Use real paths from this repo, never placeholders like `<your-repo>`.
- Derive every count, env var, model name, table name, prompt name, router name, and file list from the live tree at write time using the commands shown in each task. If a value cannot be cleanly derived, omit it rather than invent it.
- Branch name for the whole effort: `docs/overhaul`. Create it before Task 1.

---

### Task 0: Branch setup

**Files:**
- None modified. Branch creation only.

- [ ] **Step 1: Create the branch off `dev`**

```bash
git checkout dev
git pull --ff-only
git checkout -b docs/overhaul
```

Expected: switched to a new branch `docs/overhaul`.

---

### Task 1: Move `ARCHITECTURE.md` to `docs/architecture.md` and create stub

**Files:**
- Move: `ARCHITECTURE.md` → `docs/architecture.md`
- Create: `ARCHITECTURE.md` (3-line stub)
- Modify: `README.md` (footer link to `ARCHITECTURE.md` → `docs/architecture.md`)
- Modify: `CLAUDE.md` (line "See `ARCHITECTURE.md` for full system design.")
- Modify: any other tracked file that mentions `ARCHITECTURE.md` and isn't the new stub itself.

- [ ] **Step 1: List every inbound reference to `ARCHITECTURE.md`**

Run: `git grep -n 'ARCHITECTURE\.md'`

Record the hits. Every hit (other than the file being moved) must be patched in step 4. Expected hits include `README.md` and `CLAUDE.md`; there may be more.

- [ ] **Step 2: Move the file with `git mv` so history is preserved**

```bash
git mv ARCHITECTURE.md docs/architecture.md
```

- [ ] **Step 3: Create the stub at the old path**

Write `ARCHITECTURE.md`:

```markdown
# Architecture

This document has moved to [`docs/architecture.md`](docs/architecture.md).
```

Three lines exactly. Trailing newline. Nothing else.

- [ ] **Step 4: Patch every inbound reference**

For each hit recorded in step 1 (other than the new stub):
- Replace `ARCHITECTURE.md` with `docs/architecture.md` if the file is at the repo root.
- For files inside subdirectories that link relatively, calculate the correct relative path (e.g. from `dashboard/backend/...` → `../../docs/architecture.md`).

Re-run `git grep -n 'ARCHITECTURE\.md'` and confirm only the stub itself contains a self-reference. The grep should otherwise return zero hits for the bare path; all references should now read `docs/architecture.md`.

- [ ] **Step 5: Verify the stub renders and the move is clean**

Run: `git status` — expect `R  ARCHITECTURE.md -> docs/architecture.md`, plus a new `ARCHITECTURE.md` (stub), plus modified inbound-reference files.

- [ ] **Step 6: Commit**

```bash
git add ARCHITECTURE.md docs/architecture.md README.md CLAUDE.md
# Plus any other files patched in step 4.
git commit -m "$(cat <<'EOF'
docs: move ARCHITECTURE.md to docs/architecture.md, leave stub

Stub at repo root keeps external links and PR-description references
working. All inbound references in the tree updated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Accuracy-patch `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md` (whole file, in place)

- [ ] **Step 1: Re-derive every list and count from the live repo**

Run each command and record the output. The patched document must reflect these exact values.

```bash
# Agent files
ls -1 agent/

# Coordinator dir contents (decide whether to keep in tree)
ls -1 agent/coordinator/ 2>/dev/null

# Prompts
ls -1 agent/prompts/

# Routers + count
ls -1 dashboard/backend/app/routers/
ls -1 dashboard/backend/app/routers/ | grep -c '\.py$'

# Services
ls -1 dashboard/backend/app/services/

# Database tables (model definitions)
grep -h '__tablename__' dashboard/backend/app/models.py

# Frontend pages + count
ls -1 dashboard/frontend/src/pages/
ls -1 dashboard/frontend/src/pages/ | grep -c '\.svelte$'

# Frontend components + count
ls -1 dashboard/frontend/src/components/ | grep -c '\.svelte$'

# Test file count
find dashboard/backend/tests -name 'test_*.py' | wc -l
```

- [ ] **Step 2: Patch the directory tree (`Directory Structure` section)**

Replace the `agent/` subtree to list every file from `ls -1 agent/`. Specifically include any of: `audit_hook.py`, `auto_mode.py`, `launcher.py`, `run_control.py`, `tray_referral.py`, `vision.py`, `vision_analyst.py`, `vision_scoring.py`, and the `skills/` directory if present in the live `ls`.

For `agent/prompts/`, list every prompt file from `ls -1 agent/prompts/`.

For `agent/coordinator/`: if the live directory contains only `__pycache__`/`tests/__pycache__` (no `.py` source), drop it from the tree entirely. If it contains real `.py` files, keep it but list those files.

For `dashboard/backend/app/routers/`, list exactly the files from `ls -1`. Drop any router that no longer exists (notably check whether `coordinator.py` is still present). The leading count in the comment (currently "20 API routers") must match the count from step 1.

For `dashboard/frontend/src/pages/`, list the four real page files and update the leading count.

- [ ] **Step 3: Patch the `Database Schema` section**

Reconcile the table list with the `__tablename__` grep output from step 1. The leading count ("14 tables") must match. Add the `audit_log` row — purpose: append-only action audit, key fields: workflow_id, agent_id, action, payload (verify exact field names against `models.py` before writing).

- [ ] **Step 4: Patch the test-count line in the directory tree comment**

The comment currently reads `# 21 test files, 325+ tests`. Replace with the count from step 1's `find`. Remove the `325+ tests` claim unless it can be re-verified by running `pytest --collect-only -q 2>/dev/null | tail -3` and seeing the real number; otherwise drop the test-count claim entirely.

- [ ] **Step 5: Patch the `Tech Stack` row that quotes test counts**

If the row "Testing | pytest + pytest-asyncio | 325+ tests, async support" is present, update with the verified count or drop the count.

- [ ] **Step 6: Verify no other counts are stale**

Search the file for any remaining numeric claim and verify each:

```bash
grep -nE '\b[0-9]+\s+(routers?|tables?|tests?|pages?|components?|services?|files?)' docs/architecture.md
```

Update or remove any number that isn't backed by step 1.

- [ ] **Step 7: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): patch directory tree, schema, and counts to match live tree

Adds audit_log table; reconciles agent/, prompts/, routers/, services/
listings; updates page and test counts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Write `docs/concepts.md`

**Files:**
- Create: `docs/concepts.md`

This doc is the explainer; later docs link to it for terms.

- [ ] **Step 1: Verify model defaults to quote**

```bash
grep -nE 'model|opus|sonnet|haiku' agent/config/*.json agent/config/*.yaml 2>/dev/null
grep -n 'get_model\|teammate_model\|manager_model\|lead_model' agent/station_orchestrator.py
```

Record the actual default model strings for Lead / Teammates / Manager. Use these verbatim in the prose below.

- [ ] **Step 2: Verify verdict names**

```bash
git grep -nE '"(APPROVE|PR|REJECT|approve|pr|reject)"' agent/ dashboard/backend/app/routers/ dashboard/backend/app/models.py | head -30
```

Confirm the verdict tokens match what the doc claims. If any is different (e.g. lowercase, different name), use the actual tokens.

- [ ] **Step 3: Verify the audit-log story**

```bash
git log --oneline --grep='audit' -n 5
git show --stat c02cc8c 2>/dev/null | head -30
git show --stat 086abe8 2>/dev/null | head -30
grep -n 'audit_log\|append.only\|receipt' dashboard/backend/app/models.py dashboard/backend/app/routers/*.py
```

Note the table name, what fields are recorded, and where the receipt log file lives (if a file path is referenced in code).

- [ ] **Step 4: Write the file**

Create `docs/concepts.md` with this exact section structure. Each section's content must use the values verified in steps 1–3.

```markdown
# Concepts

*The mental model behind Claude Agent Station. Read this once before tweaking anything.*

## Agent Teams

Three roles, each running a different model:

| Role | Default model | Responsibility |
|------|---------------|----------------|
| Lead | <verified Sonnet model id> | Fetches eligible issues, spawns one teammate per issue, reviews plans for conflicts, monitors until all work completes |
| Teammates | <verified Opus model id> | Each works on a single GitHub issue in an isolated git worktree — reads code, plans, implements, tests, commits locally |
| Manager | <verified Sonnet model id> | Reviews all teammate work post-completion and issues a verdict |

The lead and teammates run inside a single Claude Agent SDK Agent Teams session driven by `agent/station_orchestrator.py`. The manager is a separate review pass invoked by `agent/scripts/run-manager.sh` after teammates finish.

## Verdicts

After the manager reviews completed work, every run terminates with one verdict:

| Verdict | What happens to the work |
|---------|--------------------------|
| APPROVE | Branch is pushed and merged into `dev` |
| PR      | Pull request is opened against `dev` for human review |
| REJECT  | Branch is discarded, run marked failed |

(Replace these tokens with the verified ones from step 2 if they differ.)

## Issue lifecycle

The lead picks issues that pass these filters:
- Repository is enabled in the dashboard.
- Issue is open and unassigned.
- Issue has no `backlog` label. **Issues labeled `backlog` are skipped without exception** — see `CLAUDE.md`.
- The lead respects refinement labels added by the analyst (`autonomous-agent/refined`, `autonomous-agent/analyzed`) to avoid re-analysing already-prepared issues.

Each picked issue is handed to its own teammate, which works inside a dedicated git worktree under `/home/claude-agent/workspaces/` so concurrent teammates do not collide.

## Plans

Every teammate writes a short implementation plan as its first deliverable. The lead reviews each plan before the teammate is allowed to start implementation. The lead can:
- Approve the plan and let work proceed.
- Reject the plan if it conflicts with another teammate's work or with the project vision; the teammate revises and resubmits.

This sequencing prevents two teammates from racing into incompatible changes.

## Plan-usage throttling

Claude usage is bounded by the active plan tier. The system tracks weekly token consumption in the `plan_usage_history` table. When usage approaches the tier limit, the orchestrator falls back from Opus to a smaller model for non-critical work. The dashboard surfaces current usage and the active throttle state on the Command Center page.

## Audit log

The `audit_log` table records every action taken by an agent — which agent (`agent_id` from the SDK), which workflow (`workflow_id`), the action and its payload. The log is append-only: rows are never updated or deleted. <Add the on-disk receipt-log path here if step 3 surfaced one; otherwise omit this sentence.> Use the audit log to reconstruct what a teammate did and when.

## Worktrees and isolation

Each teammate runs in its own git worktree under `/home/claude-agent/workspaces/<run-id>/<issue-number>/`. Worktrees are created per run and torn down by the stale-run reaper after the run terminates. This isolation means file edits from one teammate are invisible to others until they are committed and merged.
```

Substitute every angle-bracketed placeholder with the verified value from steps 1–3 before writing the file. If a value cannot be verified, omit the sentence containing it rather than guess.

- [ ] **Step 5: Commit**

```bash
git add docs/concepts.md
git commit -m "$(cat <<'EOF'
docs(concepts): add concepts.md explaining Agent Teams, verdicts, audit log

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Write `docs/configuration.md`

**Files:**
- Create: `docs/configuration.md`

- [ ] **Step 1: Derive the env-var table from `dashboard/backend/app/config.py`**

```bash
sed -n '/class.*Settings/,/^class\b/p' dashboard/backend/app/config.py | head -200
```

For every setting in the `Settings` class, capture: variable name (with `STATION_` prefix), default, and docstring/comment if present. These become the rows of the env-var table. If a setting has no `STATION_` prefix in its env mapping, exclude it.

- [ ] **Step 2: Derive defaults for models, schedule, budgets**

```bash
ls -1 agent/config/
cat agent/config/*.json agent/config/*.yaml 2>/dev/null | head -200
grep -n 'model\|budget\|schedule\|interval' agent/config/* 2>/dev/null
git grep -nE 'OnCalendar|OnUnitActiveSec' agent/systemd/
```

Record default model per role, default budget caps, and the systemd timer cadence.

- [ ] **Step 3: Verify project-config schema**

```bash
grep -n 'class.*Project' dashboard/backend/app/schemas.py dashboard/backend/app/models.py | head
sed -n '/class Project.*Schema/,/^class\b/p' dashboard/backend/app/schemas.py | head -80
```

Capture the real fields (repo, priority, mode, enabled, branch, plus anything else present) and their accepted values.

- [ ] **Step 4: Write the file**

```markdown
# Configuration

*Reference for every configurable setting. For operators tuning the system.*

## Where config lives

The canonical configuration store is the `config` table in `station.db` (key/value, JSON-encoded values). The dashboard writes here directly. A JSON view of the same config is materialised at `STATION_CONFIG_PATH` for the agent process to read; the dashboard's `config_sync` service keeps the two in sync. **Always edit through the dashboard or the `/api/config` endpoint** — direct edits to the JSON file are overwritten on the next sync.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
<one row per setting from step 1, every cell filled in>

## Models

Defaults per role (verified in `agent/config/`):

| Role | Default | Override key |
|------|---------|--------------|
| Lead | <verified> | `models.lead` |
| Teammate | <verified> | `models.teammate` (or `models.employee` — use whichever the live config uses) |
| Manager | <verified> | `models.manager` |

To change a model, set the corresponding key via the dashboard Config page or `PATCH /api/config`. The orchestrator picks up the change on the next run.

## Budgets and rate limits

Token budgets per run and per week are configured under `budgets.*` in `station.db`. The system tracks weekly usage in `plan_usage_history` and falls back from Opus to a smaller model when usage approaches the active plan-tier ceiling — see [`concepts.md`](concepts.md#plan-usage-throttling).

<List the actual budget keys discovered in step 2. If none, say so explicitly: "No hard budget caps are set by default; the only limit is plan-tier throttling.">

## Schedule

The agent runs on a systemd timer. Default cadence: <verified `OnCalendar` or `OnUnitActiveSec` value from step 2>. To change it, edit the timer unit under `agent/systemd/` and reload:

\`\`\`bash
sudo systemctl daemon-reload
sudo systemctl restart claude-agent.timer
\`\`\`

## Autonomy levels

Agent behaviour is gated by an autonomy-level setting. See [`adr/0001-autonomy-levels.md`](adr/0001-autonomy-levels.md) for the model and the level definitions.

## API key and webhook secret

| Setting | Purpose |
|---------|---------|
| `STATION_API_KEY` | Required for all `/api/*` requests except `/api/health` and `/api/webhook`. Bearer token in `Authorization` header, or `?api_key=` query parameter. |
| `STATION_WEBHOOK_SECRET` | Required on `/api/webhook` requests via the `X-Webhook-Token` header. Prevents external sources from injecting fake agent events. |

If neither is set, the dashboard runs unauthenticated — only suitable for a fully isolated host.

## Project config

Each managed repository is one row in the `projects` table. The dashboard's Projects page is the easiest way to edit; the underlying schema is:

| Field | Type | Description |
|-------|------|-------------|
<one row per project field from step 3>

Example JSON for the `/api/projects` POST body:

\`\`\`json
<minimal but valid example using the real fields>
\`\`\`
```

Replace every angle-bracketed placeholder with the verified value. If the system has no default budgets, say so explicitly — do not invent caps.

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md
git commit -m "$(cat <<'EOF'
docs(configuration): add configuration reference

Env vars, models, budgets, schedule, API keys, project config.
Values derived from live config and dashboard backend.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Write `docs/operations.md`

**Files:**
- Create: `docs/operations.md`

- [ ] **Step 1: Verify systemd unit names**

```bash
ls -1 agent/systemd/
```

Record every `*.service` and `*.timer` file. The dashboard service name is `claude-station-dashboard.service` per the current README — verify it matches `agent/systemd/`.

- [ ] **Step 2: Verify log paths**

```bash
git grep -n '/var/log/claude-agent\|STATION_LOG_DIR' agent/ dashboard/backend/app/ | head
```

Confirm the agent log directory and any per-run log layout used by the codebase.

- [ ] **Step 3: Verify the recovery scripts and services**

```bash
ls -1 agent/scripts/
grep -n 'class.*Reaper\|stale_run' dashboard/backend/app/services/stale_run_reaper.py
grep -n 'circuit.breaker\|3-strike\|MAX_FAILURES' agent/scripts/circuit-breaker.sh
```

Record the actual reaper service or background-task name, the circuit-breaker threshold, and where its state file lives.

- [ ] **Step 4: Write the file**

```markdown
# Operations

*When something is wrong or you need to act on the running system. For operators.*

## Service control

Three units run the system:

| Unit | Purpose |
|------|---------|
<one row per service/timer from step 1>

Common commands:

\`\`\`bash
sudo systemctl status claude-station-dashboard.service
sudo systemctl restart claude-station-dashboard.service
sudo systemctl status claude-agent.timer
sudo systemctl list-timers claude-agent.timer
\`\`\`

(Adjust unit names to match step 1's listing.)

## Log locations

| Source | Path | Read with |
|--------|------|-----------|
| Agent runs | `/var/log/claude-agent/` (override via `STATION_LOG_DIR`) | `tail -f`, or the dashboard Logs page |
| Dashboard backend | systemd journal | `journalctl -u claude-station-dashboard.service -f` |
| Audit / receipts | <verified path from concepts.md task or "see audit_log table"> | dashboard Decisions page or direct SQLite query |

## Common failures and fixes

### Stuck run / orphan recovery

Symptom: a run shows `running` indefinitely after the agent has crashed or been killed.

Fix: the stale-run reaper service runs periodically and marks orphan runs as failed. To force immediately:

\`\`\`bash
<exact command discovered in step 3, e.g. `curl -X POST http://localhost:8420/api/runs/reap` if the endpoint exists, otherwise: `sudo systemctl restart claude-station-dashboard.service`>
\`\`\`

### OAuth token expired

Symptom: agent runs fail at start with auth errors from Claude.

Fix:

\`\`\`bash
sudo -u claude-agent /opt/claude-agent-station/agent/scripts/refresh-token.py
\`\`\`

### Circuit breaker tripped

Symptom: agent timer fires but no run starts; logs mention the circuit breaker.

A repository accumulates failures via `agent/scripts/circuit-breaker.sh`. After <verified threshold> consecutive failures, the breaker trips for that repo. To clear:

\`\`\`bash
<exact rm or reset command discovered in step 3, e.g. `sudo rm /var/lib/claude-agent-station/circuit-breaker/<repo>.state`>
\`\`\`

### Plan-tier throttle

Symptom: teammates suddenly run on Sonnet/Haiku instead of Opus.

This is intentional. See [`concepts.md`](concepts.md#plan-usage-throttling). To verify, open the dashboard Command Center — current weekly usage and active throttle state are displayed there.

### Dashboard returns 401

Cause: `STATION_API_KEY` is set on the server but the request is missing the `Authorization: Bearer <key>` header. Either pass the header or unset `STATION_API_KEY` (only on isolated hosts).

## Audit trail

Every agent action is recorded append-only in the `audit_log` table. To filter by agent:

\`\`\`bash
sqlite3 /var/lib/claude-agent-station/station.db "SELECT * FROM audit_log WHERE agent_id = '<id>' ORDER BY ts;"
\`\`\`

(Adjust column names to match the real schema verified in Task 2.)

## Upgrade procedure

\`\`\`bash
cd /opt/claude-agent-station
sudo -u claude-agent git pull --ff-only
cd dashboard/backend
sudo -u claude-agent ../../venv/bin/pip install -r requirements-lock.txt
sudo systemctl restart claude-station-dashboard.service
\`\`\`

The dependency drift check runs automatically in CI; if local installs disagree with the lock file, the dashboard service will fail to start with an import error.

## Disaster recovery

### Database backup

\`\`\`bash
sqlite3 /var/lib/claude-agent-station/station.db ".backup /tmp/station.db.bak"
\`\`\`

### Database restore

Stop the dashboard, replace the file, start the dashboard:

\`\`\`bash
sudo systemctl stop claude-station-dashboard.service
sudo cp /tmp/station.db.bak /var/lib/claude-agent-station/station.db
sudo chown claude-agent:claude-agent /var/lib/claude-agent-station/station.db
sudo systemctl start claude-station-dashboard.service
\`\`\`

### Workspace cleanup

Each run creates worktrees under `/home/claude-agent/workspaces/`. The stale-run reaper cleans these as runs terminate, but to free space manually:

\`\`\`bash
sudo -u claude-agent find /home/claude-agent/workspaces/ -mindepth 1 -maxdepth 2 -mtime +7 -exec rm -rf {} +
\`\`\`
```

Replace every angle-bracketed placeholder with the verified value from steps 1–3. If a recovery command cannot be verified to exist, omit that troubleshooting section rather than invent a command.

- [ ] **Step 5: Commit**

```bash
git add docs/operations.md
git commit -m "$(cat <<'EOF'
docs(operations): add operations runbook

Service control, log locations, common failures, audit trail,
upgrade, disaster recovery.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Write `docs/install.md`

**Files:**
- Create: `docs/install.md`

- [ ] **Step 1: Read `install.sh` end to end**

```bash
wc -l install.sh
cat install.sh
```

Note: every step the script performs (user creation, dep install, systemd unit installation, SELinux, dashboard service start, OAuth setup invitation). The doc must summarise these accurately and in order.

- [ ] **Step 2: Verify the dependency-management commands**

```bash
sed -n '/Python dependency management/,/## Origin/p' README.md
```

Capture the existing pip-compile flow verbatim for inclusion in this doc.

- [ ] **Step 3: Verify `STATION_*` env vars touched at install time**

```bash
git grep -nE 'STATION_(DB_PATH|LOG_DIR|CONFIG_PATH|WORKSPACES_DIR)' install.sh dashboard/backend/app/config.py
```

Confirm which paths the install script creates and which are set by env vars.

- [ ] **Step 4: Write the file**

```markdown
# Install

*How to deploy Claude Agent Station on a fresh VM. For anyone setting up their own instance.*

## Prerequisites

- Rocky Linux 9, RHEL 9, or another systemd-based Linux distribution.
- Python 3.11 or newer.
- `git`, `jq`, and `sqlite3` available on `PATH`.
- Claude Code CLI installed for the deploying user (you will run the OAuth flow during first-run).
- A GitHub personal access token with `repo` scope.
- Root or sudo access on the host.

## Automated install

\`\`\`bash
git clone https://github.com/kenhaesler/claude-agent-station.git /opt/claude-agent-station
cd /opt/claude-agent-station
sudo bash install.sh
\`\`\`

The installer will:

<bullet list mirroring step 1 — every action `install.sh` actually performs, in order>

When it finishes, the dashboard is running on port 8420. Continue with **First-run walkthrough** below.

## Manual install

For platforms without a working `install.sh` path, or to understand what the installer does:

1. Create the service user.

   \`\`\`bash
   sudo useradd --system --home /home/claude-agent --create-home claude-agent
   \`\`\`

2. Clone the repository to `/opt/claude-agent-station` and chown to `claude-agent`.

3. Create the venv and install dependencies.

   \`\`\`bash
   cd /opt/claude-agent-station
   sudo -u claude-agent python3.11 -m venv venv
   sudo -u claude-agent venv/bin/pip install -r dashboard/backend/requirements-lock.txt
   \`\`\`

4. Install systemd units from `agent/systemd/`.

   \`\`\`bash
   sudo cp agent/systemd/*.service agent/systemd/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   \`\`\`

5. Apply the SELinux policy if your host enforces SELinux.

   \`\`\`bash
   sudo bash agent/selinux/install.sh
   \`\`\`

6. Create the data directory.

   \`\`\`bash
   sudo mkdir -p /var/lib/claude-agent-station /var/log/claude-agent
   sudo chown -R claude-agent:claude-agent /var/lib/claude-agent-station /var/log/claude-agent
   \`\`\`

7. Start the dashboard, enable the agent timer.

   \`\`\`bash
   sudo systemctl enable --now claude-station-dashboard.service
   sudo systemctl enable --now claude-agent.timer
   \`\`\`

(Adjust unit names if your `agent/systemd/` directory differs from the names listed.)

## First-run walkthrough

1. Open `http://<host>:8420/` in a browser.
2. Run the Claude OAuth flow — the dashboard's Config page has a "Sign in to Claude" button; this completes the PKCE flow and writes credentials to `STATION_CREDENTIALS_PATH`.
3. Set the GitHub token on the same Config page.
4. Add your first project on the Projects page (`owner/repo`, priority, enabled). Do **not** label any issues you want the agent to skip with `backlog`.
5. Wait for the agent timer to fire, or click "Run now" on the Command Center page.

What to expect on the first run: the lead inspects the project's open issues, picks eligible ones (see [`concepts.md`](concepts.md#issue-lifecycle)), spawns a teammate per issue. You will see live activity on the Agent Teams Canvas.

## Updating an existing install

\`\`\`bash
cd /opt/claude-agent-station
sudo -u claude-agent git pull --ff-only
cd dashboard/backend
sudo -u claude-agent ../../venv/bin/pip install -r requirements-lock.txt
sudo systemctl restart claude-station-dashboard.service
\`\`\`

If the lock file changed, the install above brings the venv into sync. CI runs a drift check that fails if `requirements-lock.txt` is out of sync with `requirements.txt`, so production never receives a mismatched pair.

## Updating Python dependencies

The backend uses a two-file pattern for reproducible installs:

| File | Purpose |
|------|---------|
| `dashboard/backend/requirements.txt` | Loose source of truth — direct deps with minimum-version bounds. Edit this. |
| `dashboard/backend/requirements-lock.txt` | Fully pinned (`==`) lock file generated by `pip-compile`, including transitive deps. Production and CI install from this. Do not hand-edit — regenerate via the command below. |
| `dashboard/backend/requirements-dev.txt` | Dev/test tooling (pytest, ruff, …); pulls in the lock file via `-r requirements-lock.txt`. |

To update or add a dependency:

\`\`\`bash
cd dashboard/backend
# 1. Edit requirements.txt (add/bump a direct dep)
# 2. Regenerate the lock file
pip install pip-tools
pip-compile --allow-unsafe --strip-extras -o requirements-lock.txt requirements.txt
# 3. Commit both files together
\`\`\`

## Uninstall

\`\`\`bash
sudo systemctl disable --now claude-agent.timer claude-station-dashboard.service
sudo rm /etc/systemd/system/claude-agent.* /etc/systemd/system/claude-station-dashboard.*
sudo systemctl daemon-reload
sudo userdel -r claude-agent
sudo rm -rf /opt/claude-agent-station /var/lib/claude-agent-station /var/log/claude-agent
\`\`\`

(Adjust unit-file globs to match the names used in step 4 of the manual install above.)
```

Substitute angle-bracketed placeholders with the bullet list derived in step 1. If `install.sh` does something not covered by the prose above, add it.

- [ ] **Step 5: Commit**

```bash
git add docs/install.md
git commit -m "$(cat <<'EOF'
docs(install): add install guide

Prerequisites, automated install, manual install, first-run
walkthrough, updates, uninstall.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Rewrite `README.md`

**Files:**
- Modify: `README.md` (whole file)

All four target docs now exist, so README links resolve.

- [ ] **Step 1: Verify model labels in CLAUDE.md and `agent/config/`**

```bash
grep -nE 'Sonnet|Opus|Haiku|claude-' CLAUDE.md
grep -nE 'Sonnet|Opus|Haiku|claude-' agent/config/* 2>/dev/null
```

Capture the exact strings to use in the architecture-diagram model labels.

- [ ] **Step 2: Confirm the four `docs/` targets exist**

```bash
ls -1 docs/install.md docs/configuration.md docs/operations.md docs/concepts.md docs/architecture.md
```

All five must list. If any is missing, the corresponding earlier task is incomplete — go back and finish it before continuing.

- [ ] **Step 3: Replace README.md with this content**

```markdown
# Claude Agent Station

[![ci](https://github.com/kenhaesler/claude-agent-station/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kenhaesler/claude-agent-station/actions/workflows/ci.yml)

Self-hosted autonomous Claude Code agent with a web dashboard.

**What it does**: Runs Claude Code agent teams on a schedule to work on your GitHub repositories — implementing features, fixing bugs, and creating issues. A lead agent coordinates teammates that each tackle a single issue. A web dashboard provides real-time visibility into team activity.

## Architecture

\`\`\`
┌──────────────────────────────────────────────┐
│             Claude Agent Station              │
│                                               │
│   Agent Teams             Web Dashboard       │
│  ┌──────────────┐      ┌──────────────┐      │
│  │  Lead Agent  │      │   FastAPI    │      │
│  │  (<verified Sonnet>) │◄────►│   + Svelte   │      │
│  │    ├─ T1     │      │   + SQLite   │      │
│  │    ├─ T2     │      └──────┬───────┘      │
│  │    └─ T3     │             │               │
│  │  (<verified Opus>)   │       :8420 (web UI)        │
│  └──────┬───────┘                             │
│         │                                     │
│    systemd timer                              │
└─────────┴─────────────────────────────────────┘
\`\`\`

Powered by the [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-code/agent-sdk) with Agent Teams. See [Concepts](docs/concepts.md) for what each role does.

## Quick start

\`\`\`bash
git clone https://github.com/kenhaesler/claude-agent-station.git /opt/claude-agent-station
cd /opt/claude-agent-station
sudo bash install.sh
# Open http://<host>:8420
\`\`\`

Full prerequisites and manual install steps: [Install guide](docs/install.md).

## Documentation

| | |
|---|---|
| [Install](docs/install.md) | Deploy on a fresh VM |
| [Configuration](docs/configuration.md) | Env vars, models, budgets, project config |
| [Operations](docs/operations.md) | Service control, logs, recovery, upgrade |
| [Concepts](docs/concepts.md) | Agent Teams, verdicts, audit log, plan throttling |
| [Architecture](docs/architecture.md) | Internal structure for contributors |

## Origin

Extracted from [claude-user-memory](https://github.com/VAMFI/claude-user-memory) autonomous mode.

## License

MIT
```

Substitute the verified model strings. The Python dep-management section, the configuration JSON example, and the dashboard feature list are intentionally removed — they live in `docs/install.md`, `docs/configuration.md`, and `docs/concepts.md` respectively.

- [ ] **Step 4: Verify every link in the new README resolves**

```bash
for link in docs/install.md docs/configuration.md docs/operations.md docs/concepts.md docs/architecture.md; do
  test -f "$link" && echo "OK $link" || echo "MISSING $link"
done
```

Every line must say `OK`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): trim to front door, link into docs/

Drops Python dep section, config JSON, dashboard feature list — all
moved into focused docs under docs/. Fixes model labels in the
diagram.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Add maintenance rule to `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current `Conventions` section**

```bash
sed -n '/## Conventions/,/^## /p' CLAUDE.md
```

- [ ] **Step 2: Append the rule after the last existing convention bullet**

Add this bullet to the `## Conventions` list (or a new `## Documentation` section if a single bullet feels out of place):

```markdown
- **Keep `docs/` in sync with code.** When you change models, env vars, DB tables, routers, or agent prompts, update the corresponding section in `docs/configuration.md`, `docs/architecture.md`, or the relevant doc. Drifted docs are a defect.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
chore(claude-md): require docs updates alongside code changes

Single rule to keep docs/ in sync with code. No CI doc-lint —
best-effort discipline only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Open the PR

**Files:**
- None modified.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin docs/overhaul
```

- [ ] **Step 2: Open the PR against `dev`**

Per memory, PRs target `dev`, never `main`.

```bash
gh pr create --base dev --title "docs: overhaul README and add user/operator docs" --body "$(cat <<'EOF'
## Summary
- Move ARCHITECTURE.md to docs/architecture.md (stub left for inbound links).
- Patch directory tree, schema (adds audit_log), and counts in docs/architecture.md.
- Add docs/install.md, docs/configuration.md, docs/operations.md, docs/concepts.md.
- Trim README.md to a front door that links into docs/.
- Add a CLAUDE.md rule requiring docs updates alongside code changes.

Spec: docs/superpowers/specs/2026-05-08-docs-overhaul-design.md
Plan: docs/superpowers/plans/2026-05-08-docs-overhaul.md

Closes nothing tracked — issues #182 and #184 were closed earlier as stale; this PR delivers the underlying intent of #184 (operator docs).

## Test plan
- [ ] All five `docs/*.md` targets exist and render on GitHub.
- [ ] Every doc link in README.md resolves.
- [ ] `git grep ARCHITECTURE.md` shows only the stub self-reference; all other refs point to `docs/architecture.md`.
- [ ] No counts in docs/architecture.md are stale (re-derive with the commands in Task 2 step 1).
- [ ] Spec checklist (acceptance criteria) all check out.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL when done.

---

## Self-review

**Spec coverage:** Every section of the spec maps to a task. Layout (Task 1), accuracy patches (Task 2), four new docs (Tasks 3–6), README rewrite (Task 7), inbound-link patches (Task 1 step 4), maintenance rule (Task 8). Verified.

**Placeholder scan:** Tasks intentionally use `<verified ...>` markers as fill-in instructions for live-derived values, with explicit verification steps that produce the value. No "TBD"/"TODO"/"add appropriate ..." patterns. Verified.

**Type/name consistency:** "teammate" used throughout (matches CLAUDE.md and current codebase); "employee" appears only in the configuration-key escape hatch ("`models.teammate` (or `models.employee` — use whichever the live config uses)") because the codebase still has `STATION_*` env vars and config keys named `employee`. Verdicts (`APPROVE`/`PR`/`REJECT`) are flagged for live verification. File paths are absolute and match the live tree.

**Spec acceptance criteria recheck:**
- README rewritten — Task 7. ✓
- `docs/architecture.md` exists, root stub — Task 1. ✓
- Four new docs — Tasks 3–6. ✓
- Inbound `ARCHITECTURE.md` references patched — Task 1 step 4. ✓
- CLAUDE.md maintenance rule — Task 8. ✓
- All counts and lists verified at write time — Task 2 step 1, Task 3 step 1, etc. ✓
- No code outside docs/README/CLAUDE/stub changed — task scope. ✓
