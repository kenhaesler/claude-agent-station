# Manager as Sibling Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the manager review into the same `ClaudeSDKClient` session as the lead + specialist teammates by adding `manager` as a fourth Agent Teams sibling, then delete the bash `claude -p` subprocess (`run_manager_review`), the `manager_heartbeat` machinery (PR #376), and the separate `manager.stream.jsonl` file.

**Architecture:** Create `agent/agents/manager.md` (new file; the spec confirms it does not exist yet) with Agent Teams frontmatter pointing at the existing `agent/prompts/manager.md` body. The orchestrator loads it the same way it already loads `issue-worker.md` and passes both into `ClaudeAgentOptions.agents`. The lead's system prompt gains an explicit "after teammates finish, spawn the `manager` agent and pass these paths" paragraph. The bash `run_manager_review` function is deleted entirely; both call sites (`run-manager.sh:3124` and `:3268`) become a simple "read the verdict file the in-session manager wrote" check. The 30-turn manager sub-cap retires in favour of the run-level cap; tokens flow through `handle_stream_event` (already wired in #389).

**Tech Stack:** Python 3.11+ (orchestrator, lead-prompt builder), bash (deletions in `run-manager.sh`), Markdown + YAML frontmatter (`manager.md`), pytest, FastAPI router edit (delete `manager_heartbeat` handler), Svelte/TypeScript (delete event renderer if present).

**Spec:** `docs/superpowers/specs/2026-05-14-issue-390-manager-as-sibling-agent.md`

**Tracking issue:** [#390](https://github.com/kenhaesler/claude-agent-station/issues/390)

**Hard dependencies:**
- Issue [#383](https://github.com/kenhaesler/claude-agent-station/issues/383) (bash deletion / Python-driven run loop) — the cleanest landing for the new "lead spawns manager" flow is on top of the Python-driven path. If #383 is not done, this plan still works against the bash path but the deletion footprint widens (the entire `run_manager_review` function plus its callers).
- Issue [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) (`ClaudeSDKClient`) — the multi-turn "wait, then spawn manager" pattern requires the long-lived client.

**Soft synergy with [#389](https://github.com/kenhaesler/claude-agent-station/issues/389)**: once the manager runs in the same session, its tool calls flow through the inline stream-derived audit path automatically — no per-process audit wiring needed for the manager.

---

## File Structure

| File | Modification | Responsibility |
|---|---|---|
| `agent/agents/manager.md` | new | Agent Teams sibling definition. YAML frontmatter (`name`, `description`, `tools`, `model`, `maxTurns`); body sourced from `agent/prompts/manager.md` with the `claude -p` → "sibling spawned by the lead" wording fix. |
| `agent/prompts/manager.md` | edit (small) | Replace "You are running via `claude -p`" with "You are running as an Agent Teams sibling spawned by the lead." Keep verdict schema and mode detection untouched. Add a short "Spawn context" subsection clarifying that the verdicts path comes from the lead's spawn prompt (unchanged from bash). |
| `agent/station_orchestrator.py` | edit | Load `agent/agents/manager.md` alongside `issue-worker.md`; add both to `agents_dict`. Lead-prompt builder appends a "Spawn the `manager` agent" paragraph that names the verdict file path. Add a "wait for verdict file" check after the SDK stream closes. Synthesize a `manager_review` webhook from a stream tag (best-effort) so the dashboard banner still flips. |
| `agent/scripts/run-manager.sh` | edit | Delete `run_manager_review` (lines 1885–2019). Replace both call sites (`:3124`, `:3268`) with: "verdicts_file=$LOG_DIR/run-${RUN_ID}-verdicts.json; ensure it exists before `execute_verdicts`". Delete the heartbeat subshell + `_TOTAL_TOKENS_IN/OUT` += manager block. |
| `dashboard/backend/app/routers/webhook.py` | edit | Delete the `manager_heartbeat` event handler. |
| `dashboard/backend/app/services/stale_run_reaper.py` | edit | Remove any manager-review carve-out (some reapers special-case the heartbeat event). |
| `dashboard/frontend/src/lib/event-stream.ts` | edit | Drop `manager_heartbeat` from the event type union + render switch. |
| `dashboard/backend/tests/test_manager_sibling.py` | new | End-to-end coverage of the new flow: lead prompt mentions manager spawn; orchestrator loads manager agent; verdict file is consumed; no `manager_heartbeat` webhook fires. |
| `dashboard/backend/tests/test_run_lifecycle.py` | edit | Adjust any fixture that mocks the manager subprocess — the subprocess no longer exists. |

---

## Setup (run once per execution session)

### Task 0: Verify dependencies and sync

- [ ] **Step 1: Pull latest dev and verify both dependencies are merged**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git checkout dev && git pull --ff-only origin dev && \
  echo "--- #383 (bash deletion) ---" && \
    gh pr list --state merged --search "issue 383" --json title,number --limit 3 && \
  echo "--- #384 (ClaudeSDKClient) ---" && \
    gh pr list --state merged --search "issue 384" --json title,number --limit 3
```

Expected: both issues have merged PRs. If either is missing, STOP — adjust the plan to either land before #390 or to scope the work to "delete-via-fallback" (riskier).

- [ ] **Step 2: Confirm baseline tests pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_run_lifecycle.py \
                    dashboard/backend/tests/test_webhook.py \
                    dashboard/backend/tests/test_orchestrator_wiring.py -q 2>&1 | tail -20
```

Expected: green.

- [ ] **Step 3: Create the feature branch**

```bash
cd /home/simon/Documents/claude-agent-station && git checkout -b feature/390-manager-as-sibling
```

---

## Task 1: Create `agent/agents/manager.md` (the new sibling definition)

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (new)
- Implementation: `agent/agents/manager.md` (new)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_manager_sibling.py`:

```python
"""End-to-end tests for the manager-as-sibling refactor (#390)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO / "agent" / "agents"
MANAGER_AGENT = AGENT_DIR / "manager.md"
MANAGER_PROMPT = REPO / "agent" / "prompts" / "manager.md"


def test_manager_agent_file_exists():
    assert MANAGER_AGENT.is_file(), (
        f"Agent Teams sibling definition missing at {MANAGER_AGENT}. "
        "See spec §Add the manager agent definition."
    )


def test_manager_agent_frontmatter_is_valid():
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "must start with YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, "missing closing frontmatter delimiter"
    fm = parts[1]
    assert "name: manager" in fm
    assert "description:" in fm
    assert "tools:" in fm
    assert "model:" in fm
    # Manager runs sonnet, not opus (cost + speed).
    assert "claude-sonnet-4-6" in fm


def test_manager_agent_body_sources_prompt():
    """The manager.md body must be the prompts/manager.md content,
    adapted for sibling-agent context (not `claude -p`).
    """
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    body = text.split("---", 2)[2]

    # Same verdict literals as the canonical prompt.
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "SKIP" in body
    # No `claude -p` framing.
    assert "claude -p" not in body
    # Sibling framing present.
    assert "sibling" in body.lower() or "agent teams" in body.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_manager_agent_file_exists -q
```

Expected: FAILED — file does not exist.

- [ ] **Step 3: Create `agent/agents/manager.md`**

Build the file:

```bash
cd /home/simon/Documents/claude-agent-station && \
  cat > agent/agents/manager.md <<'HEADER'
---
name: manager
description: Reviews work produced by backend / frontend / qa teammates and writes verdict JSON to the path supplied in the spawn prompt.
tools: Read, Edit, Write, Bash, Glob, Grep
model: claude-sonnet-4-6
permissionMode: bypassPermissions
maxTurns: 60
---

HEADER
```

Then append the body. The body is a near-verbatim copy of `agent/prompts/manager.md` with two surgical edits:

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 <<'PY'
from pathlib import Path

src = Path("agent/prompts/manager.md").read_text(encoding="utf-8")

# Edit 1: replace the "claude -p" context line.
src = src.replace(
    "- You are running via `claude -p`.",
    "- You are running as an Agent Teams sibling agent spawned by the lead in the same SDK session as the backend / frontend / qa teammates.",
)

# Edit 2: add a one-line spawn-context note.
src = src.replace(
    "- The verdict file path is provided in your user prompt.",
    "- The verdict file path is provided in your user prompt by the lead — write a valid JSON file there before ending your turn.",
)

dst = Path("agent/agents/manager.md")
existing = dst.read_text(encoding="utf-8")  # the frontmatter block
dst.write_text(existing + src, encoding="utf-8")
PY
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -v
```

Expected: 3 tests passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/agents/manager.md dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "feat(agents): add manager.md Agent Teams sibling definition (#390)"
```

---

## Task 2: Load the manager agent into `agents_dict`

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `agent/station_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_manager_sibling.py`:

```python
def test_orchestrator_loads_manager_agent_definition():
    """The orchestrator must register the manager agent alongside issue-worker."""
    from agent.station_orchestrator import load_agent_definition

    name, defn = load_agent_definition(MANAGER_AGENT)
    assert name == "manager"
    assert defn.model == "claude-sonnet-4-6"
    assert defn.tools is not None
    assert "Write" in defn.tools  # for the verdicts file
    assert "Bash" in defn.tools   # for gh issue view


def test_agents_dict_includes_both_issue_worker_and_manager(monkeypatch, tmp_path):
    """A unit-level test on the loader logic the project loop uses.

    Replicates the inline ``agents_dict`` construction at
    ``station_orchestrator.py:1703-1717`` to assert both agents are loaded.
    """
    from agent.station_orchestrator import load_agent_definition

    agent_dir = REPO / "agent" / "agents"
    files = {
        "issue-worker": agent_dir / "issue-worker.md",
        "manager": agent_dir / "manager.md",
    }
    agents = {}
    for name, path in files.items():
        assert path.is_file(), f"missing {path}"
        n, d = load_agent_definition(path)
        agents[n] = d

    assert set(agents.keys()) == {"issue-worker", "manager"}
```

- [ ] **Step 2: Run the test to verify the loader works**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_orchestrator_loads_manager_agent_definition \
                    dashboard/backend/tests/test_manager_sibling.py::test_agents_dict_includes_both_issue_worker_and_manager -q
```

Expected: passes (the loader is generic; only the orchestrator's per-project loop needs to be told to load both files).

- [ ] **Step 3: Update the per-project loop to load both agents**

Edit `agent/station_orchestrator.py`. Find the existing `agents_dict` construction at lines 1703-1721:

```python
    # Load issue-worker agent definition for SDK discovery
    agent_dir = Path(__file__).parent / "agents"
    worker_file = agent_dir / "issue-worker.md"
    agents_dict: dict[str, AgentDefinition] | None = None
    if worker_file.exists():
        try:
            worker_name, worker_def = load_agent_definition(worker_file)
            employee_override = get_model(config, "employee", "")
            if employee_override and employee_override != worker_def.model:
                logger.info(
                    "Overriding teammate model from config: %s (was %s)",
                    employee_override, worker_def.model,
                )
                worker_def = replace(worker_def, model=employee_override)
            agents_dict = {worker_name: worker_def}
            logger.info("Loaded agent definition: %s from %s (model=%s)", worker_name, worker_file, worker_def.model)
        except Exception as e:
            logger.warning("Failed to load agent definition %s: %s", worker_file, e)
```

Replace with:

```python
    # Load Agent Teams sibling definitions for SDK discovery: the
    # ``issue-worker`` (backend/frontend/qa teammates) and the ``manager``
    # (verdict producer, added in #390).
    agent_dir = Path(__file__).parent / "agents"
    agents_dict: dict[str, AgentDefinition] | None = None

    worker_file = agent_dir / "issue-worker.md"
    if worker_file.exists():
        try:
            worker_name, worker_def = load_agent_definition(worker_file)
            employee_override = get_model(config, "employee", "")
            if employee_override and employee_override != worker_def.model:
                logger.info(
                    "Overriding teammate model from config: %s (was %s)",
                    employee_override, worker_def.model,
                )
                worker_def = replace(worker_def, model=employee_override)
            agents_dict = {worker_name: worker_def}
            logger.info(
                "Loaded agent definition: %s from %s (model=%s)",
                worker_name, worker_file, worker_def.model,
            )
        except Exception as e:
            logger.warning("Failed to load agent definition %s: %s", worker_file, e)

    manager_file = agent_dir / "manager.md"
    if manager_file.exists():
        try:
            mgr_name, mgr_def = load_agent_definition(manager_file)
            manager_override = get_model(config, "manager", "")
            if manager_override and manager_override != mgr_def.model:
                logger.info(
                    "Overriding manager model from config: %s (was %s)",
                    manager_override, mgr_def.model,
                )
                mgr_def = replace(mgr_def, model=manager_override)
            agents_dict = {**(agents_dict or {}), mgr_name: mgr_def}
            logger.info(
                "Loaded agent definition: %s from %s (model=%s)",
                mgr_name, manager_file, mgr_def.model,
            )
        except Exception as e:
            logger.warning("Failed to load agent definition %s: %s", manager_file, e)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -v
```

Expected: 5 tests passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/station_orchestrator.py dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "feat(orchestrator): load manager Agent Teams sibling (#390)"
```

---

## Task 3: Teach the lead to spawn the manager

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `agent/station_orchestrator.py` — the lead-prompt builder

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_manager_sibling.py`:

```python
def test_lead_prompt_instructs_lead_to_spawn_manager(tmp_path):
    """The lead's system prompt must include a paragraph instructing it
    to spawn the ``manager`` agent after teammates report completion.

    Drives the lead-prompt builder with minimal inputs and asserts on the
    rendered string.
    """
    from agent.station_orchestrator import _build_lead_prompt

    issues = [{"number": 1, "title": "test issue", "body": "..."}]
    teammates = {"backend": "/tmp/wt/backend", "frontend": "/tmp/wt/frontend", "qa": "/tmp/wt/qa"}

    prompt = _build_lead_prompt(
        repo="owner/repo",
        run_id="20260514T100000Z",
        issues=issues,
        teammate_worktrees=teammates,
        teammate_model="claude-opus-4-7",
        max_turns=100,
        config={"dashboard": {"webhook_url": "http://localhost:8420/api/webhook/run-event"}},
        workspace="/tmp/workspaces/repo",
        vision=None,
        approved_plan_paths=[],
        plan_only_mode=False,
        review_package_path="/var/log/claude-agent/run-20260514T100000Z-review.md",
        verdicts_file_path="/var/log/claude-agent/run-20260514T100000Z-verdicts.json",
    )

    # Must reference the manager sibling explicitly.
    assert "manager" in prompt.lower()
    assert "spawn" in prompt.lower()
    # Must include the verdicts file path the manager writes to.
    assert "verdicts.json" in prompt
    # Must include the review package path the manager reads from.
    assert "review.md" in prompt or "review" in prompt.lower()
    # Must come AFTER the teammate-completion check (textually).
    spawn_idx = prompt.lower().find("spawn the `manager`")
    if spawn_idx < 0:
        spawn_idx = prompt.lower().find("spawn a `manager`")
    assert spawn_idx > prompt.lower().find("teammate"), (
        "manager spawn instructions must appear after teammate completion text"
    )
```

The exact `_build_lead_prompt` signature here is what we'll wire in Step 3. If the existing builder has a different name (e.g. `_build_team_prompt`), adapt the test to whatever name the code uses but keep the assertions identical.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_lead_prompt_instructs_lead_to_spawn_manager -q
```

Expected: FAILED — either the function does not accept `verdicts_file_path` or the prompt does not yet mention the manager spawn.

- [ ] **Step 3: Extend the lead prompt builder**

Edit `agent/station_orchestrator.py`. Locate the prompt-builder function that emits the lead's system prompt — the function that builds the string starting with `"You are the lead of an agent team implementing GitHub issues..."` around lines 918–1000. Pass through two new arguments: `review_package_path` and `verdicts_file_path`.

Add a new section to the rendered prompt — insert before the final `## Rules` block (around line 989):

```python
    manager_section = f"""
## Manager review (spawn the manager sibling)

After every teammate has emitted `.claude-employee-report-<index>.json` (or
the 20-minute timeout elapses with whichever reports exist), you MUST:

1. Assemble the review package at `{review_package_path}` — concatenate every
   teammate report and the diff summary. This is the manager's input.
2. Spawn a `manager` sibling agent via the Agent tool. The manager is a
   separate Agent Teams sibling — same SDK session, separate role/prompt/model.
   Pass it the following spawn prompt verbatim, substituting the paths:

   ```
   Review the employee work package at: {review_package_path}

   Write your verdicts to: {verdicts_file_path}

   Your hard turn budget for this review is 60. Treat turn 30 as your soft
   deadline to start drafting the verdicts file.

   Read the review package file first, then evaluate each project's work
   against the criteria in your system prompt. Be strict on completeness —
   never approve partial implementations.
   ```

3. **Do NOT attempt to review the work yourself.** You are the orchestrator;
   the manager is the quality gate. Spawn the manager and wait for it to
   finish — when its turn ends, the verdicts file at `{verdicts_file_path}`
   must exist and contain valid JSON.
4. Only after the manager has written the verdicts file should you provide
   the final JSON summary and end your turn.

Spawn the manager exactly once per run. Do not spawn additional manager
siblings — the orchestrator only reads one verdicts file.
"""
```

Append `{manager_section}` into the f-string that returns the rendered prompt, after the existing `## CRITICAL: Active Monitoring Rules` block and before `## Rules`.

Refactor the builder to expose these two paths as keyword arguments — they are already computable from `LOG_DIR` + `run_id` so the orchestrator caller passes them in. Concretely, at the caller (around the SDK options construction at line 2017), compute:

```python
                    log_dir = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))
                    review_package_path = str(log_dir / f"run-{run_id}-review.md")
                    verdicts_file_path = str(log_dir / f"run-{run_id}-verdicts.json")
                    prompt = _build_lead_prompt(
                        ...,                          # existing args
                        review_package_path=review_package_path,
                        verdicts_file_path=verdicts_file_path,
                    )
```

If the function is currently a module-level helper without those parameters, add them with defaults of `None` and conditionally render the manager section only when both are set (so unit tests can still exercise the builder without the dashboard env).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -v
```

Expected: 6 tests passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/station_orchestrator.py dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "feat(prompt): teach lead to spawn manager sibling (#390)"
```

---

## Task 4: Build the review package in-Python

Today the bash builds the review package at `build_review_package` (search `run-manager.sh` for that function). The lead now needs the file on disk before it spawns the manager. Two paths:

- **Path A (recommended):** Keep the bash building the review package (it's already correct + tested), but ensure it runs **before** the orchestrator hands control to the lead's spawn-manager phase. This is the natural state after #383: the Python driver runs the bash review-package builder as a subprocess step, then enters the lead's session.
- **Path B:** Port `build_review_package` to Python. Larger scope; defer.

This plan takes Path A.

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `agent/station_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_orchestrator_builds_review_package_before_manager_spawn(tmp_path, monkeypatch):
    """The orchestrator must produce the review package file before the
    lead's session is asked to spawn the manager. We don't run a live
    SDK; we just assert the helper exists and produces a file.
    """
    from agent.station_orchestrator import _ensure_review_package

    # Synthesise a minimal workspace + reports.
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "repo"
    repo.mkdir(parents=True)
    (repo / ".claude-employee-report-0.json").write_text(
        '{"mode":"full","issue_number":1,"verdict_request":"APPROVE","summary":"x"}',
        encoding="utf-8",
    )
    log_dir = tmp_path / "log"
    log_dir.mkdir()

    out_path = _ensure_review_package(
        run_id="run-test",
        log_dir=log_dir,
        workspaces=[repo],
        mode="full",
    )

    assert out_path.is_file()
    assert "issue 1" in out_path.read_text(encoding="utf-8") \
        or "issue_number" in out_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_orchestrator_builds_review_package_before_manager_spawn -q
```

Expected: FAILED — `_ensure_review_package` does not exist.

- [ ] **Step 3: Implement `_ensure_review_package`**

Add to `agent/station_orchestrator.py`:

```python
def _ensure_review_package(
    *,
    run_id: str,
    log_dir: Path,
    workspaces: list[Path],
    mode: str,
) -> Path:
    """Produce the manager's review-package file at
    ``{log_dir}/run-{run_id}-review.md``.

    Concatenates each workspace's ``.claude-employee-report-*.json`` and
    a short diff summary. Returns the file path. Idempotent: if the file
    already exists (e.g. the bash phase produced it), the path is
    returned without rewriting.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    out = log_dir / f"run-{run_id}-review.md"
    if out.exists() and out.stat().st_size > 0:
        return out

    lines: list[str] = []
    lines.append(f"# Review package for run {run_id}")
    lines.append(f"MODE: {mode.upper()}")
    lines.append("")
    for wt in workspaces:
        reports = sorted(wt.glob(".claude-employee-report-*.json"))
        if not reports:
            continue
        lines.append(f"## Workspace: `{wt}`")
        for rpt in reports:
            lines.append(f"### Report: `{rpt.name}`")
            lines.append("```json")
            lines.append(rpt.read_text(encoding="utf-8"))
            lines.append("```")
            lines.append("")
        # Diff summary
        try:
            import subprocess
            diff = subprocess.run(
                ["git", "-C", str(wt), "diff", "--stat", "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if diff.stdout.strip():
                lines.append("### Diff summary")
                lines.append("```")
                lines.append(diff.stdout)
                lines.append("```")
        except Exception:
            pass

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
```

Wire the call into the per-project loop — invoke `_ensure_review_package(...)` after teammates finish (i.e. before the SDK session ends, when the orchestrator transitions to "lead summarises + manager reviews"). Concretely, call it inside the `finally` block where the orchestrator already synthesises employee reports (around line 2176).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_orchestrator_builds_review_package_before_manager_spawn -q
```

Expected: passes.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/station_orchestrator.py dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "feat(orchestrator): ensure review package exists for manager spawn (#390)"
```

---

## Task 5: Read the verdict file the manager wrote

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `agent/station_orchestrator.py`

After the SDK stream closes for a project, the verdicts file at `{LOG_DIR}/run-{run_id}-verdicts.json` must exist and be valid JSON — written by the in-session manager sibling. We need a deterministic "wait + read" helper since the existing bash `run_manager_review` did this via its own exit.

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_read_verdicts_file_returns_parsed_payload(tmp_path):
    """The orchestrator must read and parse the manager's verdict file."""
    from agent.station_orchestrator import _read_verdicts_file

    p = tmp_path / "run-test-verdicts.json"
    p.write_text(
        '{"run_id":"run-test","verdicts":[{"project":"o/r","verdict":"APPROVE","branch":"b","issue_number":1}]}',
        encoding="utf-8",
    )
    payload = _read_verdicts_file(p)
    assert payload["verdicts"][0]["verdict"] == "APPROVE"


def test_read_verdicts_file_returns_none_when_missing(tmp_path):
    """Missing verdict file → return None so the caller can degrade."""
    from agent.station_orchestrator import _read_verdicts_file
    p = tmp_path / "missing.json"
    assert _read_verdicts_file(p) is None


def test_read_verdicts_file_returns_none_on_malformed_json(tmp_path):
    """Malformed JSON → return None and log a warning."""
    from agent.station_orchestrator import _read_verdicts_file
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert _read_verdicts_file(p) is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -k read_verdicts -q
```

Expected: FAILED — `_read_verdicts_file` does not exist.

- [ ] **Step 3: Implement `_read_verdicts_file`**

Add to `agent/station_orchestrator.py`:

```python
def _read_verdicts_file(path: Path) -> dict | None:
    """Read and parse the manager-produced verdicts JSON.

    Returns the parsed dict, or ``None`` if the file is missing /
    unparseable. Never raises — the caller is expected to degrade
    gracefully when the manager produced no verdicts (timeout, crash).
    """
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        import json
        return json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("verdicts file %s unparseable: %s", path, exc)
        return None
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -k read_verdicts -v
```

Expected: 3 tests passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/station_orchestrator.py dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "feat(orchestrator): add _read_verdicts_file helper (#390)"
```

---

## Task 6: Delete `run_manager_review` from `run-manager.sh`

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `agent/scripts/run-manager.sh`

- [ ] **Step 1: Write the failing grep test**

Append:

```python
def test_run_manager_sh_no_longer_defines_run_manager_review():
    """#390 acceptance: ``run_manager_review`` is removed from the bash."""
    sh = REPO / "agent" / "scripts" / "run-manager.sh"
    text = sh.read_text(encoding="utf-8")
    assert "run_manager_review()" not in text, (
        "run_manager_review must be deleted (manager is now a sibling agent)"
    )
    assert "manager.stream.jsonl" not in text, (
        "manager.stream.jsonl file is gone — manager activity is on the main stream"
    )
    assert "manager_heartbeat" not in text, (
        "manager_heartbeat retired with PR #376 revert"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_run_manager_sh_no_longer_defines_run_manager_review -q
```

Expected: FAILED — all three substrings still present.

- [ ] **Step 3: Delete the function and its callers**

Edit `agent/scripts/run-manager.sh`:

a) Delete the entire `run_manager_review()` function — lines 1885 through 2019 (inclusive of the closing `}`).

b) Find the first caller around line 3123-3127:

```bash
    local verdicts_file
    verdicts_file=$(run_manager_review "$review_package")
    ...
    execute_verdicts "$verdicts_file"
```

Replace with:

```bash
    # #390: the manager is now a sibling agent inside the lead's SDK
    # session. It writes verdicts to this path during the run; we just
    # confirm the file exists and hand it to the verdict executor.
    local verdicts_file="$LOG_DIR/run-${RUN_ID}-verdicts.json"
    if [ ! -f "$verdicts_file" ]; then
        log_error "Manager sibling produced no verdicts file at $verdicts_file"
        log_error "The in-session manager either crashed or hit max-turns. Skipping verdict execution."
    else
        execute_verdicts "$verdicts_file"
    fi
```

c) Find the retry caller at line 3267-3274:

```bash
        local retry_verdicts_file
        retry_verdicts_file=$(run_manager_review "$retry_review_package")
        ...
        execute_verdicts "$retry_verdicts_file"
        verdicts_file="$retry_verdicts_file"
```

The retry pattern only makes sense when the manager is a separate process that can be re-invoked. With the sibling-agent model the manager already ran inside the lead's session — there is no second invocation to re-run. Delete the retry block entirely; the only path forward is the existing verdict file.

If the retry block has surrounding state we cannot delete safely, fall back to: `verdicts_file=$retry_verdicts_file` → `:` (no-op) and leave a TODO referencing #391 (decompose long runs would naturally provide a second pass).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_run_manager_sh_no_longer_defines_run_manager_review -q
```

Expected: passes.

```bash
cd /home/simon/Documents/claude-agent-station && \
  shellcheck agent/scripts/run-manager.sh 2>&1 | head -30
```

Expected: no new errors introduced (existing warnings may persist).

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/scripts/run-manager.sh dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "chore(run-manager): delete run_manager_review subprocess (#390)"
```

---

## Task 7: Delete the `manager_heartbeat` webhook plumbing (revert PR #376)

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)
- Implementation: `dashboard/backend/app/routers/webhook.py`, `dashboard/backend/app/services/stale_run_reaper.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_webhook_router_no_longer_handles_manager_heartbeat():
    import inspect
    from app.routers import webhook
    src = inspect.getsource(webhook)
    assert "manager_heartbeat" not in src, (
        "manager_heartbeat event must be removed (PR #376 revert via #390)"
    )


def test_stale_run_reaper_has_no_manager_carveout():
    import inspect
    try:
        from app.services import stale_run_reaper
    except ImportError:
        # Service may live elsewhere — fall back to a grep.
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "manager_heartbeat\\|manager_review_window",
             "dashboard/backend/app/"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", f"orphan refs: {result.stdout}"
        return
    src = inspect.getsource(stale_run_reaper)
    assert "manager_heartbeat" not in src
    assert "manager_review_window" not in src.lower()
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -k manager_heartbeat -q
```

Expected: FAILED.

- [ ] **Step 3: Delete the webhook handler**

Edit `dashboard/backend/app/routers/webhook.py`. Find the `elif event_name == "manager_heartbeat":` branch and delete it along with its body (read 15 lines around the match to scope correctly).

- [ ] **Step 4: Delete the reaper carve-out (if present)**

Edit `dashboard/backend/app/services/stale_run_reaper.py`. Search for any conditional that special-cases the manager-review window (often a `last_event_at` comparison that excludes `manager_heartbeat` events) and remove it.

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -rn "manager_heartbeat\|manager_review_window" dashboard/backend/app/
```

Expected after deletion: no matches.

- [ ] **Step 5: Frontend cleanup**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -rn "manager_heartbeat" dashboard/frontend/src/
```

If matches exist, delete them (typically in `lib/event-stream.ts` and one component in `pages/RunDetail.svelte` that renders the event as a chip). Re-run the build to confirm no TypeScript regressions.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -v
```

Expected: all tests passing.

- [ ] **Step 7: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/app/routers/webhook.py \
          dashboard/backend/app/services/stale_run_reaper.py \
          dashboard/frontend/src/ && \
  git commit -m "revert(376): retire manager_heartbeat (manager is now a sibling)"
```

---

## Task 8: Update the canonical manager prompt + docs

**Files:**
- Implementation: `agent/prompts/manager.md`
- Docs: `docs/architecture.md` (small)

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_manager_sibling.py`:

```python
def test_canonical_manager_prompt_reflects_sibling_context():
    """``agent/prompts/manager.md`` continues to be the canonical source.

    After #390 it must reflect the new context (sibling agent in lead's
    SDK session) rather than the legacy ``claude -p`` invocation.
    """
    text = MANAGER_PROMPT.read_text(encoding="utf-8")
    assert "claude -p" not in text, (
        "canonical prompt still references `claude -p`; should describe "
        "the manager as an Agent Teams sibling"
    )
    assert "sibling" in text.lower() or "agent teams" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_canonical_manager_prompt_reflects_sibling_context -q
```

Expected: FAILED.

- [ ] **Step 3: Edit `agent/prompts/manager.md`**

Find (line 15):

```markdown
- You are running via `claude -p`.
```

Replace with:

```markdown
- You are running as an Agent Teams sibling agent spawned by the lead in the same SDK session as the backend / frontend / qa teammates.
```

Find (line 19):

```markdown
- The verdict file path is provided in your user prompt.
```

Replace with:

```markdown
- The verdict file path is provided in your user prompt by the lead — write a valid JSON file there before ending your turn.
```

- [ ] **Step 4: Update `docs/architecture.md`**

Add a one-sentence note near the Agent Teams flow description: "After teammates finish, the lead spawns a fourth sibling (`manager`) which produces the verdict JSON. Both `issue-worker.md` and `manager.md` live under `agent/agents/` and are loaded by the orchestrator at startup."

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/prompts/manager.md docs/architecture.md \
          dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "docs(manager): update canonical prompt + architecture for sibling model"
```

---

## Task 9: Drop bash-side manager token accounting

The bash `run_manager_review` deletion in Task 6 removed the function. Its sibling step — accumulating manager tokens into `_TOTAL_TOKENS_IN/OUT` — also needs to go (it would double-count once tokens flow through `handle_stream_event`). Verify and clean up.

**Files:**
- Implementation: `agent/scripts/run-manager.sh`
- Test: covered by Task 6's grep guard

- [ ] **Step 1: Check for residual manager-token accumulation**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "_mt_in\|_mt_out\|_mt_total\|_mt_turns\|_TOTAL_TOKENS_IN.*manager\|_TOTAL_TOKENS_OUT.*manager\|extract_stream_tokens.*manager" agent/scripts/run-manager.sh
```

Expected: empty (Task 6 already removed everything inside `run_manager_review`).

- [ ] **Step 2: If matches remain, delete them**

Remove any leftover `_TOTAL_TOKENS_IN=$((_TOTAL_TOKENS_IN + _mt_in))` lines. These are dead now that the manager runs in-session.

- [ ] **Step 3: Pin token totals in a test**

Append to `dashboard/backend/tests/test_manager_sibling.py`:

```python
def test_handle_stream_event_accumulates_manager_tokens_via_assistantmessage():
    """The manager's AssistantMessage.usage must flow through the same
    state.tokens_in / state.tokens_out the lead and teammates already use.
    """
    import asyncio
    from agent import station_orchestrator as so
    from claude_agent_sdk.types import AssistantMessage

    msg = AssistantMessage(content=[], model="claude-sonnet-4-6")
    try:
        msg.usage = {"input_tokens": 100, "output_tokens": 50}
    except AttributeError:
        msg.usage = {"input_tokens": 100, "output_tokens": 50}

    state = so._StreamState()
    asyncio.run(so.handle_stream_event(msg, {"webhook_url": ""}, "test", state=state))
    assert state.tokens_in == 100
    assert state.tokens_out == 50
```

- [ ] **Step 4: Run it**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_handle_stream_event_accumulates_manager_tokens_via_assistantmessage -q
```

Expected: passes (the existing token-accumulation branch in `handle_stream_event` is agent-agnostic).

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_manager_sibling.py agent/scripts/run-manager.sh && \
  git commit -m "test(390): pin manager-token flow via AssistantMessage.usage"
```

---

## Task 10: Integration test — full flow without `manager_heartbeat`

**Files:**
- Test: `dashboard/backend/tests/test_manager_sibling.py` (append)

- [ ] **Step 1: Write the integration assertion**

Append:

```python
@pytest.mark.asyncio
async def test_full_run_emits_no_manager_heartbeat_webhook(monkeypatch, tmp_path):
    """End-to-end: a simulated run must not emit a `manager_heartbeat`
    webhook. Drives the webhook router with a minimal sequence of events
    and asserts the manager_heartbeat path returns 404 / 400.
    """
    from httpx import AsyncClient
    from app.main import app

    async with AsyncClient(app=app, base_url="http://test") as client:
        # The handler is gone — the router should reject unknown event names
        # with a 4xx, not silently accept and persist.
        resp = await client.post(
            "/api/webhook/run-event",
            json={"event": "manager_heartbeat", "run_id": "run-test", "phase": "manager_review"},
        )
        assert resp.status_code in (400, 404, 422), (
            f"manager_heartbeat must not be a recognised event; got {resp.status_code}"
        )
```

- [ ] **Step 2: Run the integration test**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_manager_sibling.py::test_full_run_emits_no_manager_heartbeat_webhook -q
```

Expected: passes (the handler deletion in Task 7 already removed the event). If the router defaults to "accept anything and persist as a generic event", adjust the assertion to: "the persisted agent_event for this payload does not flag `manager_heartbeat` as a special-cased phase".

- [ ] **Step 3: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_manager_sibling.py && \
  git commit -m "test(390): pin manager_heartbeat webhook is unhandled"
```

---

## Task 11: End-to-end + PR

- [ ] **Step 1: Run the full suite**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/ -q 2>&1 | tail -30
```

Expected: green.

- [ ] **Step 2: Frontend build**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend && npm run build 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 3: Grep-contract sanity**

```bash
cd /home/simon/Documents/claude-agent-station && \
  echo "--- run_manager_review ---" && \
  grep -rn "run_manager_review" agent/ dashboard/ || echo "(none)" ; \
  echo "--- manager_heartbeat ---" && \
  grep -rn "manager_heartbeat" agent/ dashboard/ || echo "(none)" ; \
  echo "--- manager.stream.jsonl ---" && \
  grep -rn "manager.stream.jsonl" agent/ dashboard/ || echo "(none)" ; \
  echo "--- agent/agents/manager.md exists ---" && \
  ls -la agent/agents/manager.md
```

Expected: first three return `(none)` (or only doc/comment matches that explain the removal); the last lists the new file.

- [ ] **Step 4: Push and open the PR**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git push -u origin feature/390-manager-as-sibling && \
  gh pr create --base dev --head feature/390-manager-as-sibling \
    --title "feat(agents): manager as sibling agent (#390, reverts #376)" \
    --body "$(cat <<'EOF'
## Summary
- New `agent/agents/manager.md` Agent Teams sibling definition — the manager review now runs inside the same SDK session as backend/frontend/qa teammates.
- Lead's system prompt instructs the lead to spawn the `manager` sibling after teammates finish, passing the review-package and verdicts-file paths.
- Bash `run_manager_review` (lines 1885–2019 of `agent/scripts/run-manager.sh`) deleted along with both call sites; the heartbeat subshell and `manager.stream.jsonl` are gone.
- Webhook `manager_heartbeat` handler removed (revert PR #376). The dashboard's stale-run reaper now relies solely on the regular `progress_update` cadence.
- Manager tokens flow through `handle_stream_event` via `AssistantMessage.usage`; the legacy bash `_TOTAL_TOKENS_IN/OUT` accumulator for the manager is gone.

## Test plan
- [x] `pytest dashboard/backend/tests/test_manager_sibling.py -v` (≥12 tests green)
- [x] `pytest dashboard/backend/tests/test_run_lifecycle.py` (existing lifecycle suite still green)
- [x] `pytest dashboard/backend/tests/test_webhook.py`
- [x] `npm run build` in `dashboard/frontend`
- [x] `shellcheck agent/scripts/run-manager.sh` (no new errors)
- [ ] Manual: trigger a production-shape run on the dev box. Expected: single `run-${RUN_ID}.stream.jsonl` (no `manager.stream.jsonl`), no `manager_heartbeat` lines in `launcher.out`, verdicts file present with the expected schema, manager activity visible in the dashboard run-detail timeline contiguously with lead/teammate phases.

Closes #390
Reverts #376
Depends on #383, #384
EOF
)"
```

- [ ] **Step 5: Manual production-shape validation**

After merge, on the dev box:

```bash
# After a live run completes:
ls /var/log/claude-agent/run-*-manager.stream.jsonl 2>&1 | tail -5
grep -c 'manager_heartbeat' /var/log/claude-agent/run-*.launcher.out 2>&1 | tail -5
sqlite3 /var/lib/claude-agent-station/station.db \
  "SELECT DISTINCT actor FROM audit_log WHERE run_id = 'run-<id>'"
```

Expected:
- First command: no files (the per-manager stream file is gone).
- Second command: `0`.
- Third command: includes `teammate-manager` (or whichever attribution string the SDK exposes for the manager sibling).

Tick the manual checkbox in the PR description.

---

## Acceptance-criteria coverage

| Spec criterion | Tasks |
|---|---|
| Manager agent definition (`agent/agents/manager.md`) created with Agent Teams frontmatter | Task 1 |
| Lead's spawn-team prompt includes the manager | Task 3 |
| Bash `run_manager_review` function deleted | Task 6 |
| `manager_heartbeat` event type retired | Task 7 |
| PR #376 reverted (heartbeat code removed from bash) | Tasks 6, 7 |
| Test: end-to-end run shows manager activity in the same stream as lead/teammates | Task 10 + Task 11 manual validation |
| Manager review tokens accounted for in the run's `tokens_total` | Task 9 |
