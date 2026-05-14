# Drop `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the defensive `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT=1800000` env var setter that PR #371 added to `agent/launcher.py`, along with the regression test pinning it in place, so the launcher no longer reinforces a workaround that the `ClaudeSDKClient` migration (issue #384) made unnecessary.

**Architecture:** Pure cleanup. The launcher's `_fetch_gh_token()` and `LOG_DIR.mkdir(...)` block stays — only the `env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", ...)` line and its multi-line explanatory comment go. The conflict_resolver path (`agent/conflict_resolver/sdk_runner.py`, the last surviving `query()` call site) gains a local module-level setter + comment so the launcher stops being a global policy owner. The pinning regression test in `dashboard/backend/tests/test_orchestrator_wiring.py` is deleted; a negative assertion replaces it.

**Tech Stack:** Python 3.11+ (launcher + sdk_runner), pytest with `inspect.getsource` for source-string assertions. No frontend, no migration, no router.

**Spec:** `docs/superpowers/specs/2026-05-14-issue-392-drop-stream-close-timeout.md`

**Tracking issue:** [#392](https://github.com/kenhaesler/claude-agent-station/issues/392)

**Hard dependency:** Issue [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) (`ClaudeSDKClient` migration) must be merged before this plan starts. The plan assumes `_user_prompt_stream` and the `query()` call at `agent/station_orchestrator.py:2047` have already been replaced with `ClaudeSDKClient` lifecycle.

---

## File Structure

| File | Modification | Responsibility |
|---|---|---|
| `agent/launcher.py` | edit | Delete lines 327–339 (the multi-line explanatory comment block + the `env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")` line). |
| `agent/conflict_resolver/sdk_runner.py` | edit | If this module still calls `query()` after #384, set the env var locally as a module-level prep call (Option 2 in the spec) with a focused comment pointing to PR #371. Otherwise: no edit. |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | edit | Delete `test_launcher_sets_stream_close_timeout_in_run_env` (lines ~1643–1665). Delete the now-obsolete second sentence of `test_user_prompt_stream_yields_one_message_and_exits`'s docstring that references the env var. Add a *negative* assertion test that the env var is no longer set by the launcher. |
| `docs/configuration.md` | edit (small) | Remove any standalone mention of `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` from the operator-tunable env-var table (if present). |

---

## Setup (run once per execution session)

### Task 0: Verify the dependency landed and sync the branch

- [ ] **Step 1: Pull latest dev and confirm #384 has merged**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git checkout dev && git pull --ff-only origin dev && \
  gh pr list --state merged --search "issue 384" --json title,number,mergedAt --limit 5
```

Expected: at least one merged PR referencing #384 / `ClaudeSDKClient`. If none, STOP — the spec's hard dependency is unmet.

- [ ] **Step 2: Confirm the orchestrator no longer calls `query()` directly**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "from claude_agent_sdk import .*query\| query(prompt=" agent/station_orchestrator.py
```

Expected: no matches (the orchestrator uses `ClaudeSDKClient`). If matches remain, #384 is not fully done; STOP.

- [ ] **Step 3: Enumerate remaining `query()` call sites**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -rn "query(prompt=\|^from claude_agent_sdk import.* query\b" agent/ 2>&1
```

Expected: zero or one matches. If `agent/conflict_resolver/sdk_runner.py:95` is the only match (or there are no matches at all), proceed. If more matches exist, audit them in Task 1.

- [ ] **Step 4: Create the feature branch**

```bash
cd /home/simon/Documents/claude-agent-station && git checkout -b fix/392-drop-stream-close-timeout
```

- [ ] **Step 5: Confirm baseline tests pass**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_orchestrator_wiring.py -q 2>&1 | tail -10
```

Expected: all green (the pin test will be green here; we'll delete it shortly).

---

## Task 1: Produce the call-site audit table

This task captures the spec's first acceptance criterion ("All query() call sites audited and either migrated or documented") as a checked-in artifact so the PR description has a concrete reference.

**Files:**
- New: `docs/superpowers/notes/2026-05-14-issue-392-audit.md` (PR-description source-of-truth)

- [ ] **Step 1: Write a presence test for the audit doc**

Create `dashboard/backend/tests/test_issue_392_audit_doc.py`:

```python
"""Pin that the call-site audit doc exists and covers every relevant module.

#392 acceptance criterion: "All query() call sites audited and either
migrated or documented".
"""

from pathlib import Path

AUDIT = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "superpowers"
    / "notes"
    / "2026-05-14-issue-392-audit.md"
)


def test_audit_doc_exists_and_covers_all_call_sites():
    assert AUDIT.is_file(), f"audit doc missing at {AUDIT}"
    text = AUDIT.read_text(encoding="utf-8")
    # Every audited module must be named so reviewers can grep.
    assert "agent/station_orchestrator.py" in text
    assert "agent/conflict_resolver/sdk_runner.py" in text
    assert "agent/vision_analyst.py" in text
    # Each row must declare its disposition.
    assert "ClaudeSDKClient" in text  # for the orchestrator
    assert "subprocess" in text or "claude --print" in text  # vision_analyst
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_issue_392_audit_doc.py -q
```

Expected: FAILED — file does not exist.

- [ ] **Step 3: Create the audit document**

Write `docs/superpowers/notes/2026-05-14-issue-392-audit.md`:

```markdown
# Issue #392 — `query()` / `claude --print` call-site audit

Date: 2026-05-14
Issue: [#392](https://github.com/kenhaesler/claude-agent-station/issues/392)
Spec: `docs/superpowers/specs/2026-05-14-issue-392-drop-stream-close-timeout.md`

This audit closes the spec's first acceptance criterion: every call site
that depended on `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` is named here and
given a disposition.

| Caller | Mechanism | Disposition |
|---|---|---|
| `agent/station_orchestrator.py:2047` (pre-#384) | `async for message in query(prompt=..., options=...)` | **Migrated to `ClaudeSDKClient`** in issue #384. Lifecycle owned by `async with ClaudeSDKClient(...) as client:`. Does not consult the env var. |
| `agent/conflict_resolver/sdk_runner.py:95` | `async for message in query(prompt=..., options=...)` | **Documented**: short-lived, one-issue session. Module-local env-prep sets `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` with a focused comment pointing to PR #371. The launcher stops being the policy owner. Migration to `ClaudeSDKClient` is a separate refactor (out of scope for #392). |
| `agent/vision_analyst.py:179` | `subprocess.run(["claude", "--print", ...])` | **n/a** — one-shot CLI invocation; the stdin-close countdown does not affect a `--print` command that exits immediately after the result message. |
| `agent/scripts/run-manager.sh:1499` (lead) | `claude -p` (bash) | Deleted by issue #383 (bash phase). No env-var dependency here today; the bash inherited the launcher's env. |
| `agent/scripts/run-manager.sh:1924` (manager) | `claude -p` (bash) | Deleted by issue #390 (manager-as-sibling). No env-var dependency here. |

### Conclusion

After #384 landed, the only callers that still issue `query()` are short-lived (`sdk_runner.py`) or are one-shot CLI invocations that exit before the SDK's stdin-close countdown fires. The launcher's global env-var setter is no longer the right level for this policy. Where the env var is still needed (`sdk_runner.py`), it is set in the module that owns the call.
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_issue_392_audit_doc.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add docs/superpowers/notes/2026-05-14-issue-392-audit.md \
          dashboard/backend/tests/test_issue_392_audit_doc.py && \
  git commit -m "docs(audit): #392 query() call-site audit + presence test"
```

---

## Task 2: Move the env-var setter into `conflict_resolver/sdk_runner.py`

This task is conditional. If `agent/conflict_resolver/sdk_runner.py` no longer calls `query()` (e.g. it has been migrated to `ClaudeSDKClient` as part of #384), skip to Task 3.

**Files:**
- Test: `dashboard/backend/tests/test_conflict_resolver_sdk_runner.py` (new, small)
- Implementation: `agent/conflict_resolver/sdk_runner.py`

- [ ] **Step 1: Confirm the module still calls `query()`**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "query(prompt=\|from claude_agent_sdk import .*query" agent/conflict_resolver/sdk_runner.py
```

Expected: a match around line 95 / line 17. If empty, this task is a no-op — note "n/a after #384" in the PR description and skip to Task 3.

- [ ] **Step 2: Write the failing test**

Create `dashboard/backend/tests/test_conflict_resolver_sdk_runner.py`:

```python
"""Source-level test pinning the localised stream-close timeout setter.

After issue #392 the launcher stops setting CLAUDE_CODE_STREAM_CLOSE_TIMEOUT
globally. Modules that still use SDK `query()` must own the setter
themselves.
"""

import inspect

from agent.conflict_resolver import sdk_runner


def test_sdk_runner_sets_stream_close_timeout_locally():
    src = inspect.getsource(sdk_runner)
    assert "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" in src, (
        "sdk_runner must set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT locally "
        "now that agent.launcher no longer does (issue #392)."
    )
    # And the comment must explain why so future-us doesn't yank it again.
    assert "PR #371" in src or "stream-close" in src.lower()
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_conflict_resolver_sdk_runner.py -q
```

Expected: FAILED.

- [ ] **Step 4: Add the localised setter**

Edit `agent/conflict_resolver/sdk_runner.py`. Near the top of the file, after the existing imports, add:

```python
# This module is the last caller of the SDK's one-shot ``query()`` API
# after issue #384's ClaudeSDKClient migration. The bundled CLI begins a
# stdin-close countdown after emitting its first ResultMessage; once
# stdin closes, every PreToolUse / PostToolUse hook callback raises
# ``Error: Stream closed`` (cli.js:7552 sendRequest). The launcher used
# to set this env var globally (PR #371); after #392 it sets nothing,
# and modules that still rely on the hook lifecycle own the setter.
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")
```

If `import os` is not already at the top of the file, add it to the existing import block.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_conflict_resolver_sdk_runner.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/conflict_resolver/sdk_runner.py \
          dashboard/backend/tests/test_conflict_resolver_sdk_runner.py && \
  git commit -m "fix(conflict-resolver): own stream-close timeout locally"
```

---

## Task 3: Delete the launcher env-var setter (and the pin test)

**Files:**
- Implementation: `agent/launcher.py`
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py` (edit — delete pin test, add negative assertion)

- [ ] **Step 1: Write the failing negative-assertion test**

Replace the existing `test_launcher_sets_stream_close_timeout_in_run_env` in `dashboard/backend/tests/test_orchestrator_wiring.py` (lines ~1643–1665) with:

```python
def test_launcher_does_not_set_stream_close_timeout_in_run_env():
    """Negative regression: ``agent.launcher.trigger()`` MUST NOT set
    ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT`` in the subprocess env.

    After issue #392 the launcher stops being the policy owner. Modules
    that still depend on the bundled CLI's hook-callback lifetime own
    their own setter (see ``agent.conflict_resolver.sdk_runner``).

    This is a source-level test — we don't boot the launcher.
    """
    import inspect
    from agent import launcher

    src = inspect.getsource(launcher)
    assert 'CLAUDE_CODE_STREAM_CLOSE_TIMEOUT' not in src, (
        "agent.launcher must not set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT "
        "(issue #392). Move the setter into the module that owns the "
        "surviving query() call."
    )
```

Also edit the docstring of `test_user_prompt_stream_yields_one_message_and_exits` (a few lines above, around 1628–1633) — find:

```python
    """``_user_prompt_stream`` yields exactly one user message, then
    StopAsyncIteration. The SDK is responsible for keeping stdin open
    long enough for hooks via its ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT``
    env var (set on the orchestrator subprocess by ``agent.launcher``).
    """
```

Replace with:

```python
    """``_user_prompt_stream`` yields exactly one user message, then
    StopAsyncIteration. After issue #384 the SDK lifecycle is owned by
    ``ClaudeSDKClient`` and stdin is kept open by the client context
    manager — no env-var workaround required.
    """
```

If the surrounding heading comment (line 1623) says "Stream-closed regression", change it to "Prompt stream lifecycle".

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_orchestrator_wiring.py::test_launcher_does_not_set_stream_close_timeout_in_run_env -q
```

Expected: FAILED — the assertion `'CLAUDE_CODE_STREAM_CLOSE_TIMEOUT' not in src` is `False` because the setter is still present.

- [ ] **Step 3: Delete the env-var setter and its comment**

Edit `agent/launcher.py`. Delete lines 327–339 inclusive — the multi-line explanatory comment plus the `env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")` line:

```python
    # Bump the SDK's stream-close timeout from the 60s default. After the
    # bundled CLI emits its first ResultMessage the SDK begins a countdown
    # before closing stdin; once stdin closes, every PreToolUse /
    # PostToolUse hook callback the CLI tries to make to the Python side
    # raises ``Error: Stream closed`` (cli.js:7552 sendRequest).
    # Production hit this ~1-2 minutes into a long Agent Teams session
    # — teammates' tool calls were still happening but their hooks
    # silently failed, so audit_log rows stopped being written and
    # teammates produced no commits. 30 minutes is generous enough for
    # multi-issue Agent Teams runs without leaving stdin open
    # indefinitely. Operators can override via the env if they need
    # longer.
    env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")
```

Leave the surrounding code (the `_fetch_gh_token()` lookup above, the `STATION_RUN_ID_OVERRIDE` propagation below) intact. The deletion should join the `GH_TOKEN` block and the `hint_run_id` block with a single blank line between them.

- [ ] **Step 4: Run both tests to verify the cleanup**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_orchestrator_wiring.py -q
```

Expected: all pass. Specifically:

- `test_launcher_does_not_set_stream_close_timeout_in_run_env` passes.
- `test_user_prompt_stream_yields_one_message_and_exits` still passes (note: if #384 deleted `_user_prompt_stream`, this test may already have been removed by #384; in that case this step is a no-op).

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -rn 'CLAUDE_CODE_STREAM_CLOSE_TIMEOUT' agent/launcher.py
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/launcher.py dashboard/backend/tests/test_orchestrator_wiring.py && \
  git commit -m "chore(launcher): drop CLAUDE_CODE_STREAM_CLOSE_TIMEOUT setter (#392)"
```

---

## Task 4: Confirm no orphan references remain

**Files:**
- Test: `dashboard/backend/tests/test_issue_392_orphan_refs.py` (new, small)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_392_orphan_refs.py`:

```python
"""Grep-style test pinning the spec's grep assertions in code (#392).

The spec requires:
  - `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT agent/launcher.py` → empty
  - `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT dashboard/backend/tests/` → empty
    EXCEPT for our own audit/negative-assertion tests which reference the
    name in a docstring/comparison only.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _grep(rel_path: str) -> list[str]:
    result = subprocess.run(
        ["grep", "-rn", "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", rel_path],
        cwd=REPO, capture_output=True, text=True,
    )
    return [l for l in result.stdout.splitlines() if l]


def test_launcher_has_no_stream_close_timeout_refs():
    hits = _grep("agent/launcher.py")
    assert hits == [], f"unexpected references in agent/launcher.py: {hits}"


def test_dashboard_tests_only_reference_env_var_in_negative_assertions():
    """The only places that may name the env var under
    ``dashboard/backend/tests/`` are the negative-assertion test added in
    Task 3 and the audit doc presence test.
    """
    hits = _grep("dashboard/backend/tests")
    allowed_files = {
        "test_orchestrator_wiring.py",   # negative assertion + docstring
        "test_issue_392_orphan_refs.py", # this file
    }
    for line in hits:
        path = line.split(":", 1)[0]
        fname = path.split("/")[-1]
        assert fname in allowed_files, (
            f"unexpected reference: {line} (allowed files: {allowed_files})"
        )
```

- [ ] **Step 2: Run the test**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_issue_392_orphan_refs.py -q
```

Expected: passes (Task 3 already removed `agent/launcher.py` references; the test only allows the negative-assertion file).

- [ ] **Step 3: If it fails, fix in-place**

If a previously unknown reference shows up under `agent/` or `dashboard/backend/tests/`, treat it as in-scope: either delete it (if dead) or move the comment/test to `agent/conflict_resolver/sdk_runner.py` alongside the local setter. Re-run the test.

- [ ] **Step 4: Run the wider test suite**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/ -q 2>&1 | tail -20
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_issue_392_orphan_refs.py && \
  git commit -m "test(392): pin grep assertions for stream-close-timeout removal"
```

---

## Task 5: Trim the operator docs (if needed)

**Files:**
- Implementation: `docs/configuration.md`

- [ ] **Step 1: Check whether the env var is documented**

```bash
cd /home/simon/Documents/claude-agent-station && \
  grep -n "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" docs/
```

Expected: zero or one match. If zero, skip to Task 6.

- [ ] **Step 2: Write a presence test for the doc cleanup**

If a match exists, create `dashboard/backend/tests/test_docs_392.py`:

```python
"""Pin that the docs no longer advertise CLAUDE_CODE_STREAM_CLOSE_TIMEOUT
as an operator-tunable env var (#392).
"""

import subprocess
from pathlib import Path


def test_docs_do_not_mention_stream_close_timeout():
    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["grep", "-rn", "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "docs"],
        cwd=repo, capture_output=True, text=True,
    )
    hits = [l for l in result.stdout.splitlines() if l]
    # The audit note IS allowed to name the env var historically.
    allowed = ["docs/superpowers/notes/2026-05-14-issue-392-audit.md"]
    for line in hits:
        path = line.split(":", 1)[0]
        assert path in allowed, f"orphan doc reference: {line}"
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_docs_392.py -q
```

Expected: FAILED if there is a live docs reference outside the audit note.

- [ ] **Step 4: Delete the docs reference**

Open the offending file (likely `docs/configuration.md`) and remove the row/section that documents `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` as an operator override. Re-run the test:

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_docs_392.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add docs/ dashboard/backend/tests/test_docs_392.py && \
  git commit -m "docs(392): drop stream-close-timeout from operator env-var table"
```

---

## Task 6: End-to-end + PR

- [ ] **Step 1: Run the full affected suite once more**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -m pytest dashboard/backend/tests/test_orchestrator_wiring.py \
                    dashboard/backend/tests/test_issue_392_orphan_refs.py \
                    dashboard/backend/tests/test_issue_392_audit_doc.py \
                    dashboard/backend/tests/test_conflict_resolver_sdk_runner.py -v
```

Expected: all green. (Skip the `test_conflict_resolver_sdk_runner.py` test if Task 2 was skipped.)

- [ ] **Step 2: Manual launcher smoke**

```bash
cd /home/simon/Documents/claude-agent-station && \
  python3 -c 'import inspect, agent.launcher as l; print("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" in inspect.getsource(l))'
```

Expected: `False`.

- [ ] **Step 3: Push and open the PR**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git push -u origin fix/392-drop-stream-close-timeout && \
  gh pr create --base dev --head fix/392-drop-stream-close-timeout \
    --title "chore(launcher): drop CLAUDE_CODE_STREAM_CLOSE_TIMEOUT workaround (#392)" \
    --body "$(cat <<'EOF'
## Summary
- Delete the launcher's defensive `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT=1800000` setter (lines 327–339 of `agent/launcher.py`) — `ClaudeSDKClient` (issue #384) owns the stream lifecycle now.
- Move the env-var setter into `agent/conflict_resolver/sdk_runner.py`, the last surviving `query()` caller, with a focused comment pointing to PR #371.
- Delete the regression test that pinned the launcher behaviour; replace with a negative assertion that the env var is no longer set.
- Audit doc at `docs/superpowers/notes/2026-05-14-issue-392-audit.md` enumerates every former call site and its disposition.

## Test plan
- [x] `pytest dashboard/backend/tests/test_orchestrator_wiring.py` (negative assertion green)
- [x] `pytest dashboard/backend/tests/test_issue_392_orphan_refs.py` (no orphan references)
- [x] `pytest dashboard/backend/tests/test_issue_392_audit_doc.py` (audit doc present)
- [x] `pytest dashboard/backend/tests/test_conflict_resolver_sdk_runner.py` (if applicable)
- [x] `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT agent/launcher.py` returns empty
- [ ] Manual: trigger one full Agent Teams run on the dev box, watch `/var/log/claude-agent/run-*-launcher.out` for `[hook-cb-fail]` warnings — must remain zero.

Closes #392
Depends on #384
EOF
)"
```

- [ ] **Step 4: Watch CI**

```bash
cd /home/simon/Documents/claude-agent-station && gh pr checks --watch
```

Expected: green.

- [ ] **Step 5: Manual production-shape validation (dev box)**

After merge, on the dev box:

```bash
sudo journalctl -u claude-agent-station-runner.service -f
# (trigger a run via the dashboard)
# In another shell:
tail -F /var/log/claude-agent/run-*-launcher.out | grep -E '\[hook-cb-fail\]|Stream closed'
```

Expected: zero matches over a full run's duration. Tick the manual checkbox in the PR description with the run id evidence.

---

## Acceptance-criteria coverage

| Spec criterion | Tasks |
|---|---|
| All `query()` call sites audited and either migrated or documented | Task 1 (audit doc) + Task 2 (localised setter in `sdk_runner.py`) |
| `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` removed from `agent/launcher.py` | Task 3 |
| Test referencing the env var removed | Task 3 (delete `test_launcher_sets_stream_close_timeout_in_run_env`) + Task 4 (orphan-ref scan) |
| No regression: production run completes within `ClaudeSDKClient`'s native lifecycle | Task 6, Step 5 (manual production-shape validation) |
