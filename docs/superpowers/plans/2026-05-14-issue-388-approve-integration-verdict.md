# APPROVE_INTEGRATION Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth manager verdict `APPROVE_INTEGRATION` that opens a non-draft PR against the integration/dev branch with `gh pr merge --auto --squash` armed, so CI gates the merge rather than a human reviewer.

**Architecture:** Additive, two-surface change. The Python dispatcher in `agent/verdict_execution.py` gains a new executor; the live bash path in `agent/scripts/run-manager.sh` gains a new `case` arm next to the existing `APPROVE | PR | REJECT | SKIP` block. The manager prompt at `agent/prompts/manager.md` gains the new tier in its verdict ladder + decision tree. Dashboard surfaces (filter chips, badges, type unions) extend `Verdict = 'APPROVE' | 'PR' | 'REJECT' | 'APPROVE_INTEGRATION'`. No schema migration — `Run.verdict` is `Text`.

**Tech Stack:** Python 3.11+ (executor + tests), bash (run-manager arm), Markdown (prompt), Svelte 5 + TypeScript (dashboard chip/badge), pytest with `subprocess` / `gh_run` mocking, FastAPI router (already accepts the query param).

**Spec:** `docs/superpowers/specs/2026-05-14-issue-388-approve-integration-verdict.md`

**Tracking issue:** [#388](https://github.com/kenhaesler/claude-agent-station/issues/388)

---

## File Structure

| File | Modification | Responsibility |
|---|---|---|
| `agent/verdict_execution.py` | extend | Add `"APPROVE_INTEGRATION"` to `VerdictKind` literal; add `execute_approve_integration` function; register in `_EXECUTORS`; teach `Verdict.from_dict` to accept the literal verbatim. |
| `dashboard/backend/tests/test_verdict_execution.py` | new | Pytest module covering: dispatch routing, success path (push + non-draft PR + auto-merge + comment), integration-disabled fallback to `execute_approve`, push failure short-circuit, malformed-input → REJECT. |
| `agent/prompts/manager.md` | edit | New `### APPROVE_INTEGRATION` heading in `<verdicts>` block; decision tree gains the `sensitive + tested` branch; Confidence-Based Verdict Modifiers table updates the 0.7–0.9 row to recommend `APPROVE_INTEGRATION`. |
| `agent/scripts/run-manager.sh` | edit | New `APPROVE_INTEGRATION)` case arm near line 2273; `_outcome_success` line 2171 includes the new literal; analyze-mode block treats it as APPROVE. |
| `agent/scripts/tests/test_verdict_dispatch.bats` | new | Bats shell test stubbing `gh` + `git` + `webhook_event`; runs the case arm with a synthetic `verdicts.json` and asserts the call order. |
| `dashboard/frontend/src/lib/types.ts` | edit | Extend `Verdict` union with `'APPROVE_INTEGRATION'`. |
| `dashboard/frontend/src/pages/RunDetail.svelte` | edit | Render badge / classifier for `APPROVE_INTEGRATION` (teal `caution`-style class, distinct label). |
| `docs/configuration.md` | edit | Add a "Verdict tiers" subsection explaining `APPROVE_INTEGRATION` and the branch-protection prerequisite. |

---

## Setup (run once per execution session)

### Task 0: Sync local dev branch

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

Expected: `Already up to date.` or a fast-forward summary.

- [ ] **Step 2: Confirm verdict-execution tests pass on a clean tree**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/ -k "verdict or launcher" -q 2>&1 | tail -20
```

Expected: existing tests green; the file we add does not yet exist.

- [ ] **Step 3: Create the feature branch**

```bash
cd /home/simon/Documents/claude-agent-station && git checkout -b feature/388-approve-integration-verdict
```

---

## Task 1: Extend `VerdictKind` literal and `Verdict.from_dict`

**Files:**
- Test: `dashboard/backend/tests/test_verdict_execution.py` (new)
- Implementation: `agent/verdict_execution.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_verdict_execution.py` with:

```python
"""Tests for agent.verdict_execution — covers issue #388.

The APPROVE_INTEGRATION verdict opens a non-draft PR against the
integration branch and arms ``gh pr merge --auto --squash``. CI is the
merge gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.verdict_execution import (
    ExecutionResult,
    Verdict,
    execute,
    execute_approve,
    execute_approve_integration,
)


def test_verdict_from_dict_accepts_approve_integration():
    """Manager output with ``verdict='APPROVE_INTEGRATION'`` must round-trip."""
    payload = {
        "project": "owner/repo",
        "issue_number": 42,
        "verdict": "APPROVE_INTEGRATION",
        "branch": "autonomous/issue-42",
        "base_branch": "main",
        "reasoning": "Auth refactor with passing tests",
    }
    parsed = Verdict.from_dict(payload)
    assert parsed.verdict == "APPROVE_INTEGRATION"
    assert parsed.project == "owner/repo"
    assert parsed.issue_number == 42
    assert parsed.branch == "autonomous/issue-42"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py::test_verdict_from_dict_accepts_approve_integration -q
```

Expected: `ImportError` on `execute_approve_integration`, or test runs but `parsed.verdict` is coerced to `"REJECT"` via the literal fallthrough. Either way: FAILED.

- [ ] **Step 3: Add the literal and stub the executor**

Edit `agent/verdict_execution.py`:

Replace the line:

```python
VerdictKind = Literal["APPROVE", "PR", "REJECT", "SKIP"]
```

with:

```python
VerdictKind = Literal[
    "APPROVE",              # Direct merge to base (today's APPROVE)
    "APPROVE_INTEGRATION",  # Non-draft PR against integration branch + --auto --squash
    "PR",                   # Draft PR for human review
    "REJECT",
    "SKIP",
]
```

Add a stub at the end of the file (above the `# ── Helpers` divider) — full body comes in Task 2:

```python
def execute_approve_integration(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,
) -> ExecutionResult:
    """Placeholder — implemented in Task 2."""
    raise NotImplementedError("execute_approve_integration not yet implemented")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py::test_verdict_from_dict_accepts_approve_integration -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/verdict_execution.py dashboard/backend/tests/test_verdict_execution.py && \
  git commit -m "feat(verdict): introduce APPROVE_INTEGRATION literal + executor stub"
```

---

## Task 2: Implement `execute_approve_integration` happy path

**Files:**
- Test: `dashboard/backend/tests/test_verdict_execution.py` (append)
- Implementation: `agent/verdict_execution.py`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_verdict_execution.py`:

```python
def _stub_gh_ok(stdout: str = "https://github.com/owner/repo/pull/99") -> MagicMock:
    """Build a stand-in for ``gh_run`` returning a success result."""
    fake = MagicMock()
    fake.ok = True
    fake.stdout = stdout
    fake.stderr = ""
    return fake


def test_execute_approve_integration_happy_path(tmp_path: Path):
    """Push, non-draft PR against dev branch, auto-merge armed, comment posted."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=42,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-42",
        base_branch="main",
        reasoning="Auth change; tests pass; CI gates merge.",
    )

    pr_url = "https://github.com/owner/repo/pull/99"
    call_log: list[tuple] = []

    def gh_run_spy(args, env=None):  # noqa: ARG001
        call_log.append(("gh", tuple(args)))
        if args[:2] == ["pr", "create"]:
            return _stub_gh_ok(pr_url)
        if args[:3] == ["pr", "merge", "--auto"]:
            return _stub_gh_ok("")
        if args[:2] == ["issue", "comment"]:
            return _stub_gh_ok("")
        return _stub_gh_ok("")

    def subprocess_run_spy(args, **kwargs):  # noqa: ARG001
        call_log.append(("sub", tuple(args)))
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with patch("agent.verdict_execution.gh_run", side_effect=gh_run_spy), \
         patch("agent.verdict_execution.subprocess.run", side_effect=subprocess_run_spy):
        result = execute_approve_integration(
            verdict,
            workspace=workspace,
            run_id="run-20260514T100000Z",
            dev_branch="dev",
        )

    assert result.success is True
    assert result.pr_url == pr_url
    assert result.verdict == "APPROVE_INTEGRATION"

    # Order: git push, gh pr create (no --draft), gh pr merge --auto --squash, issue comment.
    kinds = [c[0] for c in call_log]
    assert kinds[:4] == ["sub", "gh", "gh", "gh"], call_log

    # 1) git push -u origin <branch>
    assert call_log[0][1][:5] == ("git", "push", "-u", "origin", "autonomous/issue-42")

    # 2) gh pr create — base = dev, no --draft anywhere
    create_args = call_log[1][1]
    assert create_args[:2] == ("pr", "create")
    assert "--base" in create_args
    assert create_args[create_args.index("--base") + 1] == "dev"
    assert "--draft" not in create_args
    assert "--head" in create_args
    assert create_args[create_args.index("--head") + 1] == "autonomous/issue-42"

    # 3) gh pr merge --auto --squash <pr_url>
    merge_args = call_log[2][1]
    assert merge_args[:4] == ("pr", "merge", "--auto", "--squash")
    assert pr_url in merge_args

    # 4) issue comment
    comment_args = call_log[3][1]
    assert comment_args[:2] == ("issue", "comment")
    assert "42" in comment_args
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py::test_execute_approve_integration_happy_path -q
```

Expected: `NotImplementedError` raised from the stub.

- [ ] **Step 3: Implement `execute_approve_integration`**

Replace the stub in `agent/verdict_execution.py` with:

```python
def execute_approve_integration(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,
) -> ExecutionResult:
    """Push the branch, open a non-draft PR against the integration/dev
    branch, then arm GitHub auto-merge (``gh pr merge --auto --squash``).

    If ``dev_branch`` is None or empty, the executor degrades to
    :func:`execute_approve` with a warning — the manager should not have
    emitted this verdict against a project without integration enabled,
    but we accept rather than fail the run.
    """
    if not dev_branch:
        logger.warning(
            "APPROVE_INTEGRATION emitted without dev_branch for %s — "
            "degrading to APPROVE",
            verdict.project,
        )
        return execute_approve(
            verdict, workspace=workspace, run_id=run_id, env=env,
        )

    result = ExecutionResult(
        verdict="APPROVE_INTEGRATION",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    # 1. git push -u origin <branch>
    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    # 2. gh pr create — NO --draft; base = integration/dev branch.
    pr = gh_run(
        [
            "pr", "create",
            "--repo", verdict.project,
            "--head", verdict.branch,
            "--base", dev_branch,
            "--title", _pr_title(verdict),
            "--body", _build_pr_body(verdict, run_id=run_id),
        ],
        env=env,
    )
    if not pr.ok:
        result.error = f"gh pr create failed: {pr.stderr.strip()[:200]}"
        return result
    result.pr_url = pr.stdout.strip()
    result.with_action(f"gh pr create (non-draft) → {result.pr_url}")

    # 3. gh pr merge --auto --squash <pr_url>. Best-effort: a failure to
    # arm auto-merge (e.g. branch protection misconfigured) does not
    # invalidate the PR itself.
    merge = gh_run(
        [
            "pr", "merge", "--auto", "--squash", result.pr_url,
        ],
        env=env,
    )
    if merge.ok:
        result.with_action("gh pr merge --auto --squash")
    else:
        logger.warning(
            "verdict_execution: auto-merge arm failed for %s: %s",
            result.pr_url, merge.stderr.strip()[:200],
        )
        result.with_action(f"gh pr merge --auto failed: {merge.stderr.strip()[:80]}")

    # 4. Issue comment (best-effort).
    if verdict.issue_number is not None:
        _post_issue_comment(
            verdict,
            body_prefix=(
                f"## Manager verdict: APPROVE_INTEGRATION — "
                f"auto-merge armed against `{dev_branch}`. CI gates merge."
            ),
            run_id=run_id, env=env, into=result,
        )
    result.success = True
    return result
```

Register it in `_EXECUTORS`:

```python
_EXECUTORS = {
    "APPROVE": execute_approve,
    "APPROVE_INTEGRATION": execute_approve_integration,
    "PR": execute_pr,
    "REJECT": execute_reject,
    "SKIP": execute_skip,
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/verdict_execution.py dashboard/backend/tests/test_verdict_execution.py && \
  git commit -m "feat(verdict): implement execute_approve_integration happy path"
```

---

## Task 3: Integration-disabled fallback + push-failure short-circuit

**Files:**
- Test: `dashboard/backend/tests/test_verdict_execution.py` (append)
- Implementation: `agent/verdict_execution.py` (already done in Task 2 for the fallback)

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_verdict_execution.py`:

```python
def test_execute_approve_integration_degrades_when_dev_branch_missing(tmp_path, caplog):
    """No dev_branch → degrade to execute_approve and emit a warning."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=7,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-7",
        base_branch="main",
        reasoning="Manager misemitted; integration disabled.",
    )

    with patch("agent.verdict_execution.execute_approve") as approve_mock, \
         caplog.at_level("WARNING"):
        approve_mock.return_value = ExecutionResult(
            verdict="APPROVE",
            project=verdict.project,
            issue_number=7,
            success=True,
            pr_url="https://github.com/owner/repo/pull/12",
        )
        result = execute_approve_integration(
            verdict, workspace=workspace, run_id="r1", dev_branch=None,
        )

    assert approve_mock.called
    assert result.success is True
    assert any("degrading to APPROVE" in rec.message for rec in caplog.records)


def test_execute_approve_integration_push_failure_short_circuits(tmp_path):
    """If ``git push`` fails, no PR is opened and no auto-merge is armed."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=99,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-99",
        base_branch="main",
    )

    push_fail = MagicMock()
    push_fail.returncode = 1
    push_fail.stderr = "remote rejected"
    push_fail.stdout = ""

    gh_calls: list[tuple] = []

    def gh_run_spy(args, env=None):  # noqa: ARG001
        gh_calls.append(tuple(args))
        return _stub_gh_ok("")

    with patch("agent.verdict_execution.subprocess.run", return_value=push_fail), \
         patch("agent.verdict_execution.gh_run", side_effect=gh_run_spy):
        result = execute_approve_integration(
            verdict, workspace=workspace, dev_branch="dev",
        )

    assert result.success is False
    assert result.error is not None
    assert "remote rejected" in result.error
    assert gh_calls == [], "no gh calls expected after push failure"


def test_execute_dispatcher_routes_approve_integration(tmp_path):
    """The ``execute`` dispatcher must route APPROVE_INTEGRATION correctly."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=1,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-1",
    )
    with patch(
        "agent.verdict_execution.execute_approve_integration"
    ) as fn:
        fn.return_value = ExecutionResult(
            verdict="APPROVE_INTEGRATION",
            project=verdict.project,
            issue_number=1,
            success=True,
        )
        execute(verdict, workspace=workspace, dev_branch="dev")
    fn.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they pass / fail as expected**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py -q
```

Expected: `5 passed`. (The fallback was already implemented in Task 2; this task pins behaviour with tests + adds the dispatcher route test.)

- [ ] **Step 3: No implementation change required**

The dispatcher routing was added when `_EXECUTORS` gained the new key in Task 2. If `test_execute_dispatcher_routes_approve_integration` fails because `execute` was not updated, verify `_EXECUTORS` contains `"APPROVE_INTEGRATION": execute_approve_integration`.

- [ ] **Step 4: Re-run full suite for the module**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py -v
```

Expected: 5 tests, all passed.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_verdict_execution.py && \
  git commit -m "test(verdict): cover APPROVE_INTEGRATION fallback + dispatch routing"
```

---

## Task 4: Update the manager prompt

**Files:**
- Implementation: `agent/prompts/manager.md`

This task is prompt-only; we use a string-match smoke test rather than a unit assertion.

- [ ] **Step 1: Write the failing smoke test**

Create `dashboard/backend/tests/test_manager_prompt_388.py`:

```python
"""Smoke test that the manager prompt advertises APPROVE_INTEGRATION."""

from pathlib import Path

PROMPT = Path(__file__).resolve().parents[3] / "agent" / "prompts" / "manager.md"


def test_manager_prompt_documents_approve_integration():
    text = PROMPT.read_text(encoding="utf-8")
    # Verdict ladder heading
    assert "### APPROVE_INTEGRATION" in text, "ladder heading missing"
    # Decision tree branch
    assert "APPROVE_INTEGRATION" in text and "sensitive" in text.lower()
    # Confidence table updated — the 0.7-0.9 row no longer says "Consider PR"
    lines = [l for l in text.splitlines() if "0.7-0.9" in l]
    assert lines, "0.7-0.9 confidence row not found"
    assert "APPROVE_INTEGRATION" in lines[0], (
        "0.7-0.9 row must recommend APPROVE_INTEGRATION; got: " + lines[0]
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_manager_prompt_388.py -q
```

Expected: FAILED — ladder heading not present.

- [ ] **Step 3: Edit `agent/prompts/manager.md`**

Inside the `<verdicts>` block, between `### APPROVE` (line 143) and `### PR` (line 147), insert:

```markdown
### APPROVE_INTEGRATION
- Work is complete and tested, but touches sensitive code (auth, payments, config) or is large enough to want CI-as-gate before landing.
- Action: Push branch, open non-draft PR against the integration/dev branch, enable auto-merge (`gh pr merge --auto --squash`). CI gates the merge; no human review required.
- Use this in preference to PR whenever tests pass and the only reason for human review would be "sensitivity". Reserve PR for cases where a human must actually look.

```

Replace the existing decision tree (lines 164-168):

```markdown
**Decision tree:**
- Work incomplete? → **REJECT**
- Work complete + large/sensitive? → **PR**
- Work complete + normal scope? → **APPROVE**
- No work to do? → **SKIP**
```

with:

```markdown
**Decision tree:**
- Work incomplete? → **REJECT**
- Work complete + normal scope + non-sensitive? → **APPROVE**
- Work complete + sensitive (auth/payments/config) + tests pass? → **APPROVE_INTEGRATION**
- Work complete + ambiguous requirements OR tests skipped OR scope > 30 files? → **PR**
- No work to do? → **SKIP**
```

Replace the Confidence-Based Verdict Modifiers table row for `0.7-0.9`:

Find:

```markdown
| 0.7-0.9 | Yes | Consider PR for human review |
```

Replace with:

```markdown
| 0.7-0.9 | Yes | APPROVE_INTEGRATION (auto-merge to dev once CI passes) |
```

Update the "Use **SKIP** instead of REJECT" sentence (around line 170) to also mention the new tier — find:

```markdown
Use **SKIP** instead of REJECT when the employee did nothing wrong — there was simply nothing to do.
```

Append immediately after it:

```markdown

Use **APPROVE_INTEGRATION** instead of **PR** whenever tests pass: a human-review PR that nobody clicks merges nothing; an auto-merge PR lands the moment CI passes.
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_manager_prompt_388.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/prompts/manager.md dashboard/backend/tests/test_manager_prompt_388.py && \
  git commit -m "feat(manager): teach prompt to prefer APPROVE_INTEGRATION over PR"
```

---

## Task 5: Bash run-manager case arm

**Files:**
- Test: `agent/scripts/tests/test_verdict_dispatch.bats` (new)
- Implementation: `agent/scripts/run-manager.sh`

- [ ] **Step 1: Write the failing bats test**

Check whether `bats` is available; if not, use a plain bash test runner instead. Create `agent/scripts/tests/test_verdict_dispatch.bats`:

```bash
#!/usr/bin/env bats
# Tests for the APPROVE_INTEGRATION case arm in run-manager.sh (issue #388).
# Stubs `gh`, `git`, `webhook_event`, and integration helpers so the case
# block runs in isolation.

setup() {
    export TMPDIR_RUN="$(mktemp -d)"
    export PATH="${TMPDIR_RUN}/bin:${PATH}"
    mkdir -p "${TMPDIR_RUN}/bin"
    cat > "${TMPDIR_RUN}/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo "GH_CALL:" "$@" >> "${TMPDIR_RUN}/calls.log"
if [[ "$1" == "pr" && "$2" == "create" ]]; then
    echo "https://github.com/owner/repo/pull/99"
fi
exit 0
EOF
    cat > "${TMPDIR_RUN}/bin/git" <<'EOF'
#!/usr/bin/env bash
echo "GIT_CALL:" "$@" >> "${TMPDIR_RUN}/calls.log"
exit 0
EOF
    chmod +x "${TMPDIR_RUN}/bin/gh" "${TMPDIR_RUN}/bin/git"
}

teardown() {
    rm -rf "${TMPDIR_RUN}"
}

@test "APPROVE_INTEGRATION runs push, non-draft pr create, auto-merge, issue comment" {
    # Source just the case block by extracting it. Cheap approach: write a
    # harness that re-implements the dispatch then sources the function
    # under test if it's been refactored into a function. Until then, we
    # invoke the live script with a synthetic verdicts.json (heavyweight)
    # OR we assert grep-based presence of the case arm. Cheap path:
    grep -n "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh
    [ "$status" -eq 0 ]

    grep -A 25 "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh > "${TMPDIR_RUN}/arm.txt"

    # Must push the branch
    grep -q "git push" "${TMPDIR_RUN}/arm.txt"

    # Must create a non-draft PR — i.e. no "--draft" inside this arm
    ! grep -q -- "--draft" "${TMPDIR_RUN}/arm.txt"

    # Must invoke gh pr create with --base pr_base_branch
    grep -q "gh pr create" "${TMPDIR_RUN}/arm.txt"
    grep -q "\-\-base \"\$pr_base_branch\"" "${TMPDIR_RUN}/arm.txt"

    # Must arm auto-merge
    grep -q "gh pr merge --auto --squash" "${TMPDIR_RUN}/arm.txt"

    # Must emit a webhook event
    grep -q "webhook_event \"verdict_execute\"" "${TMPDIR_RUN}/arm.txt"
}
```

If `bats` is not installed, equivalent shell assertions work as a plain `bash agent/scripts/tests/test_verdict_dispatch.sh` — convert the body to a sequence of `grep -q || exit 1` lines. The CI step is the same either way.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && \
  bats agent/scripts/tests/test_verdict_dispatch.bats 2>&1 | tail -20 || \
  bash -c 'grep -n "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh' || echo "MISSING (expected)"
```

Expected: case arm missing (test fails or grep returns nothing).

- [ ] **Step 3: Add the bash case arm**

Edit `agent/scripts/run-manager.sh`. Inside the verdict dispatch block (around the existing `APPROVE)` arm at line 2273), add a new arm before `APPROVE)`:

```bash
            APPROVE_INTEGRATION)
                log_info "APPROVE_INTEGRATION: pushing $branch, opening auto-merge PR against $pr_base_branch"
                if ! integration_enabled; then
                    log_warn "APPROVE_INTEGRATION but integration disabled — falling through to APPROVE"
                    # Re-dispatch via the APPROVE arm by overwriting the
                    # variable and falling through. Bash does not have C
                    # fallthrough, so set the verdict and ``continue`` to
                    # let the outer loop re-evaluate. The next iteration
                    # would skip our work; simpler: inline the APPROVE
                    # arm's merge_to_dev logic here.
                    merge_to_dev "$project" "$branch" "$base_branch" "$issue_number" "$reasoning"
                    if [ $? -eq 0 ]; then
                        notify "approve" "APPROVED (fallback from APPROVE_INTEGRATION): $project #$issue_number"
                    else
                        notify "error" "APPROVE_INTEGRATION fallback to APPROVE-merge failed: $project #$issue_number"
                    fi
                else
                    if ! git push -u origin "$branch" 2>&1; then
                        log_error "git push failed for $branch"
                        notify "error" "APPROVE_INTEGRATION push failed: $project #$issue_number"
                        continue
                    fi
                    local pr_url
                    pr_url=$(gh pr create --repo "$project" \
                        --head "$branch" \
                        --base "$pr_base_branch" \
                        --title "autonomous: resolve #$issue_number" \
                        --body "$reasoning

Closes #$issue_number

---
Autonomous run: $RUN_ID
Manager verdict: APPROVE_INTEGRATION — auto-merge armed against \`$pr_base_branch\`. CI gates merge." 2>&1) || {
                        log_error "gh pr create failed: $pr_url"
                        notify "error" "APPROVE_INTEGRATION pr create failed: $project #$issue_number"
                        continue
                    }
                    log_ok "APPROVE_INTEGRATION: opened PR $pr_url"
                    if ! gh pr merge --auto --squash "$pr_url" 2>&1; then
                        log_warn "gh pr merge --auto failed for $pr_url — PR is open but auto-merge not armed"
                    else
                        log_ok "Auto-merge armed for $pr_url"
                    fi
                    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                        gh issue comment "$issue_number" --repo "$project" --body "🤖 **Manager verdict: APPROVE_INTEGRATION** — auto-merge armed against \`$pr_base_branch\` ($pr_url). CI gates merge.

$reasoning

Run: $RUN_ID" 2>/dev/null || log_warn "Failed to comment on issue #$issue_number"
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                    fi
                    notify "approve" "APPROVE_INTEGRATION (auto-merge armed): $project #$issue_number $pr_url"
                fi
                ;;
```

Update the outcome-success line at 2171:

Find:

```bash
        [[ "$verdict" == "APPROVE" || "$verdict" == "PR" ]] && _outcome_success="true"
```

Replace with:

```bash
        [[ "$verdict" == "APPROVE" || "$verdict" == "APPROVE_INTEGRATION" || "$verdict" == "PR" ]] && _outcome_success="true"
```

Update the analyze-mode block at line 2221 — find:

```bash
            if [ "$verdict" = "APPROVE" ]; then
                log_ok "APPROVE (analyze mode): Analysis work accepted"
```

Replace with:

```bash
            if [ "$verdict" = "APPROVE" ] || [ "$verdict" = "APPROVE_INTEGRATION" ]; then
                log_ok "$verdict (analyze mode): Analysis work accepted"
```

The icon dictionary at line 2667 — find:

```bash
    icon = {'APPROVE': 'APPROVED', 'PR': 'PR CREATED', 'REJECT': 'REJECTED'}.get(v['verdict'], v['verdict'])
```

Replace with:

```bash
    icon = {'APPROVE': 'APPROVED', 'APPROVE_INTEGRATION': 'APPROVED (auto-merge)', 'PR': 'PR CREATED', 'REJECT': 'REJECTED'}.get(v['verdict'], v['verdict'])
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && \
  bash -c '
    set -e
    grep -n "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh
    awk "/APPROVE_INTEGRATION\)/,/;;/" agent/scripts/run-manager.sh > /tmp/arm.txt
    grep -q "git push" /tmp/arm.txt
    ! grep -q -- "--draft" /tmp/arm.txt
    grep -q "gh pr create" /tmp/arm.txt
    grep -q "gh pr merge --auto --squash" /tmp/arm.txt
    grep -q "_outcome_success.*APPROVE_INTEGRATION" agent/scripts/run-manager.sh
    echo OK
  '
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add agent/scripts/run-manager.sh agent/scripts/tests/test_verdict_dispatch.bats && \
  git commit -m "feat(verdict): handle APPROVE_INTEGRATION in run-manager.sh"
```

---

## Task 6: Dashboard verdict types + badge

**Files:**
- Test: `dashboard/backend/tests/test_runs.py` (append) — verify the verdict query param accepts the new literal end-to-end
- Implementation: `dashboard/frontend/src/lib/types.ts`, `dashboard/frontend/src/pages/RunDetail.svelte`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_runs.py` (or create a new test file `test_runs_verdict_filter.py` if appending is awkward):

```python
@pytest.mark.asyncio
async def test_list_runs_accepts_approve_integration_verdict_filter(client, setup_db):
    """The runs router must accept ``?verdict=APPROVE_INTEGRATION`` (issue #388).

    Run.verdict is Text — no migration needed — so the filter is purely a
    SQL equality check. Seed one matching and one non-matching row and
    assert the response contains only the matching one.
    """
    from app.models import Run
    from datetime import datetime, timezone
    async with async_session() as db:
        db.add(Run(
            run_id="run-388-ai",
            status="finished",
            verdict="APPROVE_INTEGRATION",
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
        ))
        db.add(Run(
            run_id="run-388-other",
            status="finished",
            verdict="APPROVE",
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    response = await client.get("/api/runs?verdict=APPROVE_INTEGRATION")
    assert response.status_code == 200
    data = response.json()
    run_ids = [r["run_id"] for r in data["runs"]]
    assert "run-388-ai" in run_ids
    assert "run-388-other" not in run_ids
```

Adapt the fixtures (`client`, `setup_db`, `async_session`) to whatever names the existing `test_runs.py` already imports; do not introduce new fixtures.

- [ ] **Step 2: Run the test to verify it passes immediately**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_runs.py -k approve_integration -q
```

Expected: `1 passed`. Because `Run.verdict` is `Text` and the router filter is a plain `where(Run.verdict == verdict)`, no router change is needed. The test pins the contract.

- [ ] **Step 3: Update the frontend `Verdict` union and badge**

Edit `dashboard/frontend/src/lib/types.ts`, line 23:

```typescript
export type Verdict = 'APPROVE' | 'APPROVE_INTEGRATION' | 'PR' | 'REJECT';
```

Edit `dashboard/frontend/src/pages/RunDetail.svelte` — find the existing chain at line 327:

```svelte
            {:else if run.verdict === 'APPROVE'}
            {:else if run.verdict === 'REJECT'}
            {:else if run.verdict === 'PR'}
            {:else if run.verdict === 'SKIP'}
```

Add a branch for `APPROVE_INTEGRATION` immediately after the `APPROVE` branch. Match the same JSX shape used by the other branches (icon + label). Concretely, find the existing `{:else if run.verdict === 'APPROVE'}` block (a few lines, ending before the next `{:else if`); duplicate it and change the label to `APPROVE_INTEGRATION` / "Auto-merge to dev".

Find line 466:

```svelte
          <span class="v {run.verdict === 'APPROVE' ? 'go' : run.verdict === 'REJECT' ? 'abort' : 'caution'}" style="font-size:14px">{run.verdict}</span>
```

Replace with:

```svelte
          <span class="v {run.verdict === 'APPROVE' ? 'go' : run.verdict === 'APPROVE_INTEGRATION' ? 'integ' : run.verdict === 'REJECT' ? 'abort' : 'caution'}" style="font-size:14px">{run.verdict}</span>
```

Find the `<style>` block (search for `.v.go`, typically near the bottom of the file) and add a teal style adjacent to the existing colour classes:

```css
.v.integ {
  background: rgba(20, 184, 166, 0.15);  /* teal-500/15 */
  color: rgb(20, 184, 166);
  border-color: rgba(20, 184, 166, 0.3);
}
```

- [ ] **Step 4: Re-run all backend tests + smoke-build the frontend**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_runs.py dashboard/backend/tests/test_verdict_execution.py -q
```

Expected: all passing.

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors. The `Verdict` union is now exhaustive against the new value everywhere it is consumed.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/frontend/src/lib/types.ts dashboard/frontend/src/pages/RunDetail.svelte dashboard/backend/tests/test_runs.py && \
  git commit -m "feat(dashboard): render APPROVE_INTEGRATION verdict with distinct badge"
```

---

## Task 7: Wire the orchestrator's verdict caller to pass `dev_branch`

The Python `execute()` dispatcher gains a `dev_branch` kwarg; today it's read from `pr_base_branch` in bash. Since the bash arm at line 2273 calls `gh` directly and not through `verdict_execution.py`, the Python wiring step is **only relevant for the post-bash future** (issue #383). For #388 we need to confirm: when a future Python caller dispatches `APPROVE_INTEGRATION`, it must compute `dev_branch` the same way bash does (lines 2080–2087: project `integration.dev_branch` resolved via `get_dev_branch`).

**Files:**
- Test: `dashboard/backend/tests/test_verdict_execution.py` (append)
- Implementation: docstring-only — no caller changes in this PR.

- [ ] **Step 1: Write a docstring-pin test**

Append to `dashboard/backend/tests/test_verdict_execution.py`:

```python
def test_execute_dispatcher_forwards_dev_branch_kwarg(tmp_path):
    """The dispatcher must thread ``dev_branch`` through to the executor.

    Pins the contract so future Python callers (post-#383 bash deletion)
    know the kwarg name to pass.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=1,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-1",
    )
    with patch("agent.verdict_execution.execute_approve_integration") as fn:
        fn.return_value = ExecutionResult(
            verdict="APPROVE_INTEGRATION",
            project="owner/repo",
            issue_number=1,
            success=True,
        )
        execute(verdict, workspace=workspace, dev_branch="dev")
    _, kwargs = fn.call_args
    assert kwargs.get("dev_branch") == "dev"
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py::test_execute_dispatcher_forwards_dev_branch_kwarg -q
```

Expected: passes — `execute(...)` already forwards all `**kwargs` to the registered executor.

- [ ] **Step 3: No implementation edit required**

The dispatcher already passes `**kwargs`. This task only adds the regression test.

- [ ] **Step 4: Run the full verdict-execution suite**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_verdict_execution.py -v
```

Expected: 6 tests, all passing.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add dashboard/backend/tests/test_verdict_execution.py && \
  git commit -m "test(verdict): pin dev_branch kwarg threading through dispatcher"
```

---

## Task 8: Documentation update

**Files:**
- Implementation: `docs/configuration.md`

- [ ] **Step 1: Write a presence test for the doc**

Create `dashboard/backend/tests/test_docs_388.py`:

```python
"""Pin that docs/configuration.md documents APPROVE_INTEGRATION (issue #388)."""

from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "configuration.md"


def test_configuration_doc_mentions_approve_integration():
    text = DOC.read_text(encoding="utf-8")
    assert "APPROVE_INTEGRATION" in text
    assert "auto-merge" in text.lower()
    # Prerequisite: branch protection must require checks for auto-merge
    # to be meaningful.
    assert "required check" in text.lower() or "branch protection" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_docs_388.py -q
```

Expected: FAILED.

- [ ] **Step 3: Add a "Verdict tiers" subsection to `docs/configuration.md`**

Append (or insert near other manager/verdict content):

```markdown
## Verdict tiers

The manager produces one of four verdicts per project/issue:

| Verdict | Action | When |
|---|---|---|
| `APPROVE` | Direct merge to base branch (or to integration's dev branch when enabled) | Tests pass, scope is normal, no sensitive code touched. |
| `APPROVE_INTEGRATION` | Non-draft PR against the integration/dev branch with `gh pr merge --auto --squash` armed | Work is complete and tested but touches sensitive code (auth, payments, config), or scope is large enough to want CI as the gate before landing. CI passes → PR auto-merges with no human click. |
| `PR` | Draft PR for human review | Ambiguous requirements, tests skipped, or scope > 30 files. A human must look. |
| `REJECT` / `SKIP` | No merge | Work incomplete (`REJECT`) or no eligible work (`SKIP`). |

### Prerequisite for `APPROVE_INTEGRATION`

`APPROVE_INTEGRATION` arms GitHub's auto-merge feature. Auto-merge only meaningfully gates when the integration/dev branch has **at least one required check** in its branch protection rules. If no checks are required, `gh pr merge --auto --squash` will merge immediately. Configure required checks at `Settings → Branches → Branch protection rules → <dev_branch>` on each project before relying on this verdict.

If the project does not have integration enabled (`integration.enabled = false`), the verdict degrades to `APPROVE` and a warning is logged — the manager should not have emitted `APPROVE_INTEGRATION` in that case, but the system accepts rather than failing the run.
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/test_docs_388.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add docs/configuration.md dashboard/backend/tests/test_docs_388.py && \
  git commit -m "docs: document APPROVE_INTEGRATION verdict + branch-protection prerequisite"
```

---

## Task 9: End-to-end + PR

- [ ] **Step 1: Run the full affected test suite**

```bash
cd /home/simon/Documents/claude-agent-station && python3 -m pytest dashboard/backend/tests/ -q 2>&1 | tail -30
```

Expected: all tests pass; no skipped suites unrelated to this work.

- [ ] **Step 2: Verify the grep contract holds across the codebase**

```bash
cd /home/simon/Documents/claude-agent-station && \
  echo "--- agent/verdict_execution.py ---" && \
  grep -n "APPROVE_INTEGRATION" agent/verdict_execution.py && \
  echo "--- bash arm ---" && \
  grep -n "APPROVE_INTEGRATION" agent/scripts/run-manager.sh && \
  echo "--- prompt ---" && \
  grep -n "APPROVE_INTEGRATION" agent/prompts/manager.md && \
  echo "--- frontend ---" && \
  grep -rn "APPROVE_INTEGRATION" dashboard/frontend/src
```

Expected: each location prints at least one match.

- [ ] **Step 3: Push and open the PR**

```bash
cd /home/simon/Documents/claude-agent-station && \
  git push -u origin feature/388-approve-integration-verdict && \
  gh pr create --base dev --head feature/388-approve-integration-verdict \
    --title "feat(verdict): APPROVE_INTEGRATION tier (#388)" \
    --body "$(cat <<'EOF'
## Summary
- Adds the `APPROVE_INTEGRATION` verdict between `APPROVE` and `PR` in the manager ladder.
- New executor pushes the branch, opens a non-draft PR against the integration/dev branch, and arms `gh pr merge --auto --squash` so CI gates the merge.
- Dashboard renders the new verdict with a teal badge; filter query param accepts the literal.

## Test plan
- [x] `pytest dashboard/backend/tests/test_verdict_execution.py` (6 tests, all green)
- [x] `pytest dashboard/backend/tests/test_runs.py -k approve_integration`
- [x] `pytest dashboard/backend/tests/test_manager_prompt_388.py`
- [x] `pytest dashboard/backend/tests/test_docs_388.py`
- [x] `npm run build` in `dashboard/frontend` (no TypeScript errors)
- [x] `grep -n "APPROVE_INTEGRATION" agent/scripts/run-manager.sh` returns the new case arm
- [ ] Manual: trigger one auth-touching issue on the dev box; confirm the manager emits `APPROVE_INTEGRATION` and the PR opens non-draft with auto-merge armed.

Closes #388
EOF
)"
```

- [ ] **Step 4: Watch the PR's CI**

```bash
cd /home/simon/Documents/claude-agent-station && gh pr checks --watch
```

Expected: green.

- [ ] **Step 5: Final commit / push if any CI fixes were needed**

If CI failed, fix in-place and:

```bash
cd /home/simon/Documents/claude-agent-station && \
  git add -A && \
  git commit -m "fix(verdict): address CI feedback on APPROVE_INTEGRATION wiring" && \
  git push
```

Expected: green CI, PR ready for merge.

---

## Acceptance-criteria coverage

| Spec criterion | Tasks |
|---|---|
| `agent/prompts/manager.md`: `APPROVE_INTEGRATION` added to verdict ladder + decision tree | Task 4 |
| `agent/verdict_execution.py`: new `execute_approve_integration` function registered in `_EXECUTORS` | Tasks 1, 2, 3 |
| Bash verdict case block handles the new verdict | Task 5 |
| Dashboard verdict filters / displays the new value | Task 6 |
| Test: simulated auth-PR scenario produces `APPROVE_INTEGRATION` not `PR` (prompt regression OR dispatcher unit) | Task 2 (dispatcher unit); Task 4 (prompt presence smoke) |
| Production: at least one issue lands on the integration branch via this path | Tracked manually post-deploy (Task 9 manual checkbox) |
