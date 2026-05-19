# Auto-Close GitHub Issues on APPROVE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When verdict execution merges teammate work onto the integration branch with an `APPROVE` or `APPROVE_INTEGRATION` verdict, automatically close the GitHub issue(s) the work addresses — using the union of `verdict.issue_number` and branch-name-extracted issue numbers.

**Architecture:** All changes in `agent/verdict_execution.py`. One regex constant, one pure utility `_resolve_issue_numbers(verdict) -> list[int]`, one helper `_close_issues(verdict, pr_url, run_id, env, into)`. Wired into `execute_approve` and `execute_approve_integration` after the existing `_post_issue_comment` call. Idempotent via `gh issue close` semantics — no run-level state. Best-effort: failures logged at WARNING, never crash the verdict.

**Tech Stack:** Python 3.11 / pytest / `gh` CLI via existing `agent.gh_client.gh_run` wrapper.

**Spec:** `docs/superpowers/specs/2026-05-19-auto-close-issues-design.md`
**Issue:** Closes #460
**Target branch:** PR `--base dev` (per project policy)

---

## File Structure

| File | Role |
|---|---|
| `agent/verdict_execution.py` | ADD module-level `_BRANCH_ISSUES_RE` constant near other regex constants. ADD `_resolve_issue_numbers(verdict)` pure helper. ADD `_close_issues(verdict, *, pr_url, run_id, env, into)` helper mirroring `_post_issue_comment`. CALL `_close_issues` from `execute_approve` (around line 180) and `execute_approve_integration` (around line 395), gated on `result.pr_url` truthy. ~50 lines added. |
| `dashboard/backend/tests/test_verdict_execution.py` | EXTEND with 10 new tests covering: regex behavior (4 cases), close called on APPROVE / APPROVE_INTEGRATION (2 cases), multi-issue branch (1), REJECT / SKIP do NOT close (2), gh-failure swallowed (1). |
| `docs/architecture.md` | ADD a brief note in the Verdict execution section documenting the new behavior. |

---

## Task 1: Regex constant + `_resolve_issue_numbers` pure helper

**Files:**
- Modify: `agent/verdict_execution.py` (add module-level regex + helper after the existing `_BRANCH_ISSUES_RE`-adjacent helpers)
- Test: `dashboard/backend/tests/test_verdict_execution.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_verdict_execution.py`:

```python
# --- #460: auto-close issue resolution ---


def test_resolve_issue_numbers_from_multi_issue_branch():
    """Branch like 'feature/backend-issues-29-30-...' yields [29, 30],
    deduplicated and sorted. Verdict.issue_number is unioned."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _make_verdict(
        overrides={"branch": "feature/backend-issues-29-30-20260519T080446Z",
                   "issue_number": 30},
    )
    assert _resolve_issue_numbers(v) == [29, 30]


def test_resolve_issue_numbers_from_old_convention():
    """Branch like 'autonomous/issue-31' with matching verdict number
    yields [31] (deduped)."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _make_verdict(
        overrides={"branch": "autonomous/issue-31", "issue_number": 31},
    )
    assert _resolve_issue_numbers(v) == [31]


def test_resolve_issue_numbers_falls_back_to_verdict_only():
    """Branch with no number pattern yields just verdict.issue_number."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _make_verdict(
        overrides={"branch": "feature/no-numbers-here", "issue_number": 42},
    )
    assert _resolve_issue_numbers(v) == [42]


def test_resolve_issue_numbers_empty_when_no_source():
    """No branch match AND verdict.issue_number is None → []."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _make_verdict(
        overrides={"branch": "feature/no-numbers-here", "issue_number": None},
    )
    assert _resolve_issue_numbers(v) == []
```

Note: `_make_verdict` is the existing fixture at the top of `test_verdict_execution.py` — confirm it accepts an `overrides` dict.

- [ ] **Step 2: Verify `_make_verdict` accepts overrides**

Run: `grep -n "_make_verdict\|overrides" dashboard/backend/tests/test_verdict_execution.py | head -15`

Expected: `_make_verdict(overrides: dict | None = None, ...)` or similar pattern. If `overrides` is named differently (e.g. `**kwargs`), adjust the test calls accordingly. The fixture is already in the file from earlier tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -xvs -k "resolve_issue_numbers"`

Expected: 4 FAIL with `ImportError: cannot import name '_resolve_issue_numbers'` (function not yet defined).

- [ ] **Step 4: Add the regex constant + helper**

Edit `agent/verdict_execution.py`. Find a good location for the new module-level regex — near the top with the other module-level constants, or right above `_pr_title` around line 431 (the existing private helper block). Add:

```python
import re  # if not already imported at top of file

# #460: extract issue numbers from branch names. Supports both
# 'feature/{role}-issues-29-30-...' (multi-issue, group 1 captures '29-30')
# and 'autonomous/issue-31' (single-issue, group 2 captures '31').
_BRANCH_ISSUES_RE = re.compile(r"\bissues?-(\d+(?:-\d+)*)\b|/issue-(\d+)\b")


def _resolve_issue_numbers(verdict: Verdict) -> list[int]:
    """Return the union of branch-name-extracted issue numbers and
    verdict.issue_number, deduplicated and sorted ascending.

    Real-world data shows ``verdict.issue_number`` is unreliable —
    multi-issue branches like ``feature/backend-issues-29-30-...`` are
    routinely emitted with only one of the numbers in the field, or
    None. The branch name is a more reliable source. #460.
    """
    numbers: set[int] = set()
    if verdict.issue_number is not None:
        numbers.add(verdict.issue_number)
    for match in _BRANCH_ISSUES_RE.finditer(verdict.branch or ""):
        multi = match.group(1)
        single = match.group(2)
        if multi:
            for chunk in multi.split("-"):
                if chunk.isdigit():
                    numbers.add(int(chunk))
        elif single and single.isdigit():
            numbers.add(int(single))
    return sorted(numbers)
```

Verify `import re` is at the top of the file. If `agent/verdict_execution.py` doesn't already import `re`, add it to the imports block.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -xvs -k "resolve_issue_numbers"`

Expected: PASS (4 tests).

- [ ] **Step 6: Run broader sweep to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -x 2>&1 | tail -5`

Expected: all PASS (existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add agent/verdict_execution.py dashboard/backend/tests/test_verdict_execution.py
git commit -m "$(cat <<'EOF'
feat(verdict_execution): _resolve_issue_numbers pure helper (#460)

New private helper that returns the union of branch-name-extracted
issue numbers and verdict.issue_number, deduplicated and sorted.
Supports both branch conventions seen in the wild: multi-issue
'feature/{role}-issues-29-30-...' and single-issue
'autonomous/issue-31'.

This is the foundation for auto-closing issues on APPROVE / APPROVE_INTEGRATION
(next commit wires the close call). No behavior change yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_close_issues` helper + wire into executors

**Files:**
- Modify: `agent/verdict_execution.py` — add `_close_issues` helper after `_post_issue_comment`; call from `execute_approve` (after `_post_issue_comment`) and `execute_approve_integration` (after `_post_issue_comment`).
- Test: `dashboard/backend/tests/test_verdict_execution.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_verdict_execution.py`:

```python
def test_execute_approve_closes_issue_after_pr_created(tmp_path):
    """APPROVE verdict triggers `gh issue close` after PR creation."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="APPROVE",
                      overrides={"branch": "autonomous/issue-42",
                                 "issue_number": 42})
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        # PR create returns a URL; subsequent gh calls succeed.
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        result = execute(v, workspace=tmp_path, run_id="run-test")
    # Find the gh issue close invocation among the mock calls.
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:3] == ["issue", "close", "42"]
    ]
    assert close_calls, f"Expected `gh issue close 42`, got: {[c.args[0] for c in mock_gh.call_args_list]}"
    assert result.success is True


def test_execute_approve_integration_closes_after_merge_armed(tmp_path):
    """APPROVE_INTEGRATION verdict also triggers `gh issue close`."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="APPROVE_INTEGRATION",
                      overrides={"branch": "autonomous/issue-42",
                                 "issue_number": 42})
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        result = execute(v, workspace=tmp_path, run_id="run-test",
                         dev_branch="autonomous/dev")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:3] == ["issue", "close", "42"]
    ]
    assert close_calls, "Expected `gh issue close 42` on APPROVE_INTEGRATION"
    assert result.success is True


def test_close_issues_handles_multi_issue_branch(tmp_path):
    """A branch addressing multiple issues closes ALL of them."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="APPROVE",
                      overrides={"branch": "feature/backend-issues-29-30-20260519T080446Z",
                                 "issue_number": 30})
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        execute(v, workspace=tmp_path, run_id="run-test")
    issue_numbers_closed = sorted({
        call.args[0][2]
        for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    })
    assert issue_numbers_closed == ["29", "30"], (
        f"Expected both 29 and 30 closed, got: {issue_numbers_closed}"
    )


def test_execute_reject_does_not_close_issue(tmp_path):
    """REJECT verdict must NOT close the issue."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="REJECT",
                      overrides={"branch": "autonomous/issue-42",
                                 "issue_number": 42})
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        execute(v, workspace=tmp_path, run_id="run-test")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    ]
    assert not close_calls, (
        f"REJECT must not close issues, got close calls: {close_calls}"
    )


def test_execute_skip_does_not_close_issue(tmp_path):
    """SKIP verdict must NOT close the issue."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="SKIP",
                      overrides={"branch": "autonomous/issue-42",
                                 "issue_number": 42})
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        execute(v, workspace=tmp_path, run_id="run-test")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    ]
    assert not close_calls, "SKIP must not close issues"


def test_close_issues_swallows_gh_failure(tmp_path):
    """If `gh issue close` fails (e.g. already-closed), verdict still
    succeeds and a WARNING is logged."""
    from agent.verdict_execution import execute
    v = _make_verdict(verdict_kind="APPROVE",
                      overrides={"branch": "autonomous/issue-42",
                                 "issue_number": 42})

    def gh_side_effect(args, env=None):
        # PR create returns a URL; everything else (issue comment + issue close)
        # also OK, EXCEPT `issue close` which fails.
        if args[:2] == ["issue", "close"]:
            return _fail_gh_result(stderr="error: issue is already closed")
        return _ok_gh_result(stdout="https://github.com/x/y/pull/1")

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=gh_side_effect):
        result = execute(v, workspace=tmp_path, run_id="run-test")

    # Verdict still succeeds despite close failure.
    assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -xvs -k "closes_issue or does_not_close or multi_issue or swallows_gh"`

Expected: 6 FAIL — no `gh issue close` calls happening yet.

- [ ] **Step 3: Add `_close_issues` helper after `_post_issue_comment`**

Edit `agent/verdict_execution.py`. Find `_post_issue_comment` (around line 437). Immediately AFTER its body ends, add:

```python
def _close_issues(
    verdict: Verdict,
    *,
    pr_url: str,
    run_id: str | None,
    env: dict[str, str] | None,
    into: ExecutionResult,
) -> None:
    """Close every issue addressed by this verdict via `gh issue close`.

    Best-effort. Each failure is logged at WARNING and the helper
    continues to the next issue. The verdict's success state is
    never affected.

    Closes the union of branch-name-extracted issue numbers and
    ``verdict.issue_number`` (see :func:`_resolve_issue_numbers`).
    Idempotent via ``gh`` — already-closed issues return an error
    that we swallow.

    #460.
    """
    issue_numbers = _resolve_issue_numbers(verdict)
    if not issue_numbers:
        return

    # Build the close comment once.
    body_parts = ["Closed by autonomous run"]
    if run_id:
        body_parts.append(run_id)
    if pr_url:
        body_parts.append(f"via PR {pr_url}")
    body = " ".join(body_parts) + "."

    for issue_number in issue_numbers:
        result = gh_run(
            [
                "issue", "close", str(issue_number),
                "--repo", verdict.project,
                "--reason", "completed",
                "--comment", body,
            ],
            env=env,
        )
        if result.ok:
            into.with_action(f"gh issue close #{issue_number}")
        else:
            logger.warning(
                "verdict_execution: gh issue close failed for %s#%s: %s",
                verdict.project, issue_number, result.stderr.strip()[:200],
            )
```

- [ ] **Step 4: Wire `_close_issues` into `execute_approve`**

Edit `agent/verdict_execution.py`. Find `execute_approve` (line 124). The current end of its body is:

```python
    # 3. issue comment (best-effort; do not fail the verdict on this)
    if verdict.issue_number is not None:
        _post_issue_comment(verdict, body_prefix="## Manager verdict: APPROVED",
                            run_id=run_id, env=env, into=result)
    result.success = True
    return result
```

Change to:

```python
    # 3. issue comment (best-effort; do not fail the verdict on this)
    if verdict.issue_number is not None:
        _post_issue_comment(verdict, body_prefix="## Manager verdict: APPROVED",
                            run_id=run_id, env=env, into=result)

    # 4. issue close (best-effort; #460). Gated on pr_url so we only
    # close when there's a PR to point at — guards against degraded
    # paths where gh pr create succeeded silently with no output.
    if result.pr_url:
        _close_issues(verdict, pr_url=result.pr_url, run_id=run_id,
                      env=env, into=result)

    result.success = True
    return result
```

- [ ] **Step 5: Wire `_close_issues` into `execute_approve_integration`**

Edit `agent/verdict_execution.py`. Find `execute_approve_integration` (line 312). Around line 395, the current end of its body looks like:

```python
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

Add the close call immediately after the comment block (the line numbers may differ slightly — anchor on the `_post_issue_comment` call and `result.success = True`):

```python
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

    # 5. Issue close (best-effort; #460).
    if result.pr_url:
        _close_issues(verdict, pr_url=result.pr_url, run_id=run_id,
                      env=env, into=result)

    result.success = True
    return result
```

- [ ] **Step 6: Run new tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -xvs -k "closes_issue or does_not_close or multi_issue or swallows_gh"`

Expected: PASS (6 tests).

- [ ] **Step 7: Run broader sweep to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -x 2>&1 | tail -5`

Expected: all PASS. If a pre-existing test like `test_approve_pushes_branch_then_creates_pr_then_comments` fails because the mock now sees additional `gh_run` calls beyond what it asserted, examine carefully — the existing test should still work because it doesn't restrict to ONLY those calls. If it does fail, the existing assertion needs to be a "calls include these" rather than a strict equality. Update it minimally if so.

- [ ] **Step 8: Commit**

```bash
git add agent/verdict_execution.py dashboard/backend/tests/test_verdict_execution.py
git commit -m "$(cat <<'EOF'
feat(verdict_execution): auto-close GitHub issues on APPROVE / APPROVE_INTEGRATION (#460)

After execute_approve / execute_approve_integration successfully push
the branch + create the PR (and arm auto-merge for the integration
path), the executor now closes the GitHub issue(s) addressed by the
verdict via `gh issue close --reason completed --comment "..."`.

Close target list is the union of verdict.issue_number AND any issue
numbers extracted from the branch name (handles multi-issue branches
like 'feature/backend-issues-29-30-...' that arrive with only one
number in verdict.issue_number).

Idempotent via gh — already-closed issues return an error that we
swallow at WARNING. Verdict success is never affected by close
failures.

Gated on result.pr_url so degraded paths (PR creation succeeded
silently with no output) don't post empty-link comments. REJECT,
SKIP, and PR verdicts deliberately leave issues open.

Solves the long-standing gap where the agent station merged work
onto the integration branch but left issues OPEN because GitHub's
auto-close only fires on default-branch merges. Verified live on
laboef1900/next-itsm issues #29 and #30, which had to be closed
manually after 6 successful APPROVE_INTEGRATION verdicts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md` — the Verdict execution section.

- [ ] **Step 1: Locate the verdict-execution section**

Run: `grep -n "Verdict execution\|verdict_execution\|APPROVE_INTEGRATION\|execute_verdict" docs/architecture.md | head -10`

Expected: at least one match in a section describing the verdict execution pipeline. If no dedicated section exists, look for "Run modes" or "Agent Teams flow" sections that mention verdict execution.

- [ ] **Step 2: Add the auto-close note**

Insert a brief paragraph in the verdict-execution section (or create one if absent). Use this content verbatim:

```markdown
**Auto-close on APPROVE (#460):** After `execute_approve` /
`execute_approve_integration` successfully push the branch + create
the PR, the executor closes the GitHub issue(s) addressed by the
verdict via `gh issue close --reason completed`. Close targets are
the union of `verdict.issue_number` AND issue numbers extracted from
the branch name (handles multi-issue branches like
`feature/backend-issues-29-30-...`). Best-effort: `gh issue close`
failures are logged at WARNING and ignored. REJECT, SKIP, and PR
verdicts leave issues open by design.

This is necessary because per-teammate PRs target the
`claude-agent-station` integration branch, not `main` — GitHub's
auto-close on `Closes #N` PR footers only fires when the PR merges
into the default branch.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): document auto-close on APPROVE (#460)

Brief note in the Verdict execution section describing the new
auto-close behavior: union of verdict.issue_number + branch-name
extraction, best-effort gh issue close, restricted to APPROVE
and APPROVE_INTEGRATION verdicts. Keeps docs in lockstep with
the implementation per CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Full test sweep

**Files:** none (validation only).

- [ ] **Step 1: Focused scope**

Run:

```bash
cd dashboard/backend && python3 -m pytest tests/test_verdict_execution.py -xvs 2>&1 | tail -10
```

Expected: all PASS. New tests + existing tests all green.

- [ ] **Step 2: Broader backend sweep**

Run:

```bash
cd dashboard/backend && python3 -m pytest tests/ \
  --ignore=tests/test_database.py \
  --ignore=tests/test_migration_script.py \
  --ignore=tests/test_pubsub.py \
  -x 2>&1 | tail -5
```

Expected: passes ≥ previous merge's 1502, 1 skipped.

- [ ] **Step 3: No commit** (validation only).

---

## Task 5: Push + open PR + post-merge live verification

**Files:** none (workflow).

- [ ] **Step 1: Push branch**

Run:

```bash
git push -u origin <branch-name>
```

- [ ] **Step 2: Open PR against `dev`**

```bash
gh pr create --base dev --title "feat: auto-close GitHub issues on APPROVE / APPROVE_INTEGRATION (#460)" --body "$(cat <<'EOF'
## Summary

Closes #460. When verdict execution succeeds on APPROVE or APPROVE_INTEGRATION, the executor now closes the GitHub issue(s) addressed by the verdict — fixing the long-standing gap where per-teammate PRs targeting the \`claude-agent-station\` integration branch never auto-closed issues (because GitHub's auto-close only fires on default-branch merges).

## Spec & plan

- Spec: \`docs/superpowers/specs/2026-05-19-auto-close-issues-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-19-auto-close-issues.md\`

## Changes by file

- \`agent/verdict_execution.py\` — new \`_BRANCH_ISSUES_RE\` constant, \`_resolve_issue_numbers(verdict) -> list[int]\` pure helper (union + dedup of verdict.issue_number and branch-name-extracted numbers), \`_close_issues(verdict, pr_url, run_id, env, into)\` helper mirroring \`_post_issue_comment\`. Wired into \`execute_approve\` and \`execute_approve_integration\` after the existing issue-comment call. Gated on \`result.pr_url\` truthy.
- \`docs/architecture.md\` — brief note in the Verdict execution section.

## Defense

- **Idempotent via gh** — \`gh issue close\` on already-closed issues returns an error swallowed at WARNING.
- **Best-effort** — close failures never crash the verdict.
- **Restricted to APPROVE / APPROVE_INTEGRATION** — REJECT, SKIP, and PR (draft) verdicts leave issues open.
- **Multi-issue branches handled** — \`feature/backend-issues-29-30-...\` closes both 29 and 30.

## Tests

10 new unit tests in \`test_verdict_execution.py\`:
- 4 covering \`_resolve_issue_numbers\` (multi-issue, old convention, fall-back, empty case).
- 2 asserting \`gh issue close\` is called after PR creation (APPROVE + APPROVE_INTEGRATION).
- 1 asserting multi-issue branch closes ALL referenced issues.
- 2 asserting REJECT and SKIP do NOT close.
- 1 asserting gh-failure is swallowed and verdict still succeeds.

**Results:** broader backend sweep clean.

## Smoke test (post-merge, NOT in CI)

1. Rebuild containers (\`docker compose build dashboard agent && docker compose up -d\`).
2. Trigger a run on a project with an open issue.
3. Wait for the run to complete with at least one APPROVE verdict.
4. Verify the corresponding GitHub issue is CLOSED with a comment linking to the PR + run_id.

## Closes

Closes #460

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge — rebuild and smoke**

```bash
git fetch origin && git reset --hard origin/dev
docker compose build dashboard agent && docker compose up -d --force-recreate dashboard agent
```

Trigger a run (use a project with open issues you're willing to have closed automatically):

```bash
API_KEY=$(grep '^STATION_API_KEY=' .env | cut -d= -f2)
curl -s -X POST http://localhost:8420/api/runs/trigger \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json'
```

After the run completes (~20–30 min), confirm:

```bash
# Pick an issue number you expect to have been closed by an APPROVE verdict
gh issue view <issue-number> --repo <owner>/<repo> --json state,closedAt,comments \
  -q '"state=" + .state + " closedAt=" + (.closedAt // "null")'
```

Expected: `state=CLOSED` with a recent `closedAt`. The most recent comment should be the autonomous-run close comment.

If the issue isn't closed after an APPROVE verdict, check the agent log for the WARNING:

```bash
docker logs cas-agent 2>&1 | grep "gh issue close failed"
```

Common causes: insufficient `GH_TOKEN` permissions, target issue already closed, repo doesn't allow programmatic close. Each is logged but does NOT fail the run.

If all checks pass, close #460:

```bash
MERGE_COMMIT=$(gh pr view <PR-NUMBER> --json mergeCommit -q .mergeCommit.oid | cut -c1-10)
gh issue close 460 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via live smoke run."
```

---

## Self-Review

**Spec coverage:**

- `_BRANCH_ISSUES_RE` regex constant ✅ Task 1.
- `_resolve_issue_numbers` pure helper ✅ Task 1.
- `_close_issues` helper mirroring `_post_issue_comment` ✅ Task 2.
- Wire into `execute_approve` ✅ Task 2 Step 4.
- Wire into `execute_approve_integration` ✅ Task 2 Step 5.
- NOT wired into `execute_pr` / `execute_reject` / `execute_skip` ✅ (untouched; tests 8, 9 confirm).
- Idempotent via gh ✅ Test 10.
- Best-effort failure swallowing ✅ Test 10.
- Multi-issue dedup ✅ Test 7.
- Close comment links to PR + run_id ✅ Task 2 Step 3 body assembly.
- `result.pr_url` gating ✅ Task 2 Steps 4 and 5.
- Docs update ✅ Task 3.
- Live verification ✅ Task 5 Step 3.

**Placeholder scan:** No TBD/TODO. All code blocks complete. Commands have expected output.

**Type consistency:**
- `_resolve_issue_numbers(verdict: Verdict) -> list[int]` matches across Tasks 1 and 2.
- `_close_issues(verdict, *, pr_url, run_id, env, into)` matches across Task 2 Steps 3, 4, 5.
- `_BRANCH_ISSUES_RE` referenced consistently.

**One pattern detail worth flagging for the implementer:**

The existing test `test_approve_pushes_branch_then_creates_pr_then_comments` may assert on a specific sequence of `gh_run` calls. After this change, an additional `gh issue close` call appears at the end. The test must allow for additional calls — verify before committing Task 2 that it does (it likely uses `mock_gh.call_args_list` with specific indices rather than exact-equality). If it asserts exact equality, update the assertion to be a "contains these calls in order" check, NOT a strict equality.

Self-review clean.
