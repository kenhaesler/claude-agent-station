# Stale-Branch Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete local branches whose referenced GitHub issues are all CLOSED at workspace setup, and update the lead's spawn prompt to require the picker before resuming any local work — unblocking the autonomous loop from indefinitely re-evaluating dead branches.

**Architecture:** All changes in two files. (A) `agent/workspace_setup.py` gains a private `_prune_stale_branches(workspace, repo, base_branch, env)` helper called from `ensure_workspace` after `git worktree prune`. (B) `agent/station_orchestrator.py::build_team_prompt` gains a `## Picker rules (#462)` section. Reuses `_BRANCH_ISSUES_RE` from PR #461 for branch-name parsing — single source of truth. Fail-soft: cleanup errors never block the run; conservative on unknown issue state (preserves).

**Tech Stack:** Python 3.11 / pytest / `gh` CLI / `git` CLI

**Spec:** `docs/superpowers/specs/2026-05-19-stale-branch-cleanup-design.md`
**Issue:** Closes #462
**Target branch:** PR `--base dev` (per project policy)

---

## File Structure

| File | Role |
|---|---|
| `agent/workspace_setup.py` | ADD private `_prune_stale_branches(workspace, project_repo, base_branch, env)` helper (~60 lines). CALL it from `ensure_workspace` after the existing `git worktree prune` line (around line 101), wrapped in `try/except Exception` for fail-soft. Import `_BRANCH_ISSUES_RE` from `agent.verdict_execution` inside the helper (lazy import — keeps top-level imports clean). |
| `agent/station_orchestrator.py` | ADD `## Picker rules (#462)` block to `build_team_prompt`. ~7 lines of literal prompt text inserted near the existing READ FIRST block (around line 1100). |
| `dashboard/backend/tests/test_workspace_setup.py` | EXTEND with 6 new tests covering the helper. |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | EXTEND with 1 snapshot test for the picker-rules section. |
| `docs/architecture.md` | ADD a brief note in the workspace-setup section (or near it) about the new pruning behavior. |

---

## Task 1: `_prune_stale_branches` helper — list-and-classify pass

**Files:**
- Modify: `agent/workspace_setup.py`
- Test: `dashboard/backend/tests/test_workspace_setup.py` (extend)

This task implements the helper and wires it into `ensure_workspace`. Six tests cover the entire surface.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_workspace_setup.py`:

```python
# --- #462: stale-branch cleanup ---


def _build_git_branch_dispatch(branches_output: str, issue_states: dict):
    """Build a subprocess.run side_effect that:
      - Returns ``branches_output`` for ``git branch --format=...``.
      - Returns ``issue_states[N]`` JSON for ``gh issue view N ...`` calls.
      - Returns a no-op success for everything else (git clone, fetch, checkout,
        pull, worktree prune, git branch -D).
    
    ``issue_states`` is keyed by issue number (int) with values either
    'OPEN', 'CLOSED', or None (None simulates a gh query failure).
    """
    deleted_branches = []

    def dispatch(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # git branch list
        if isinstance(cmd, list) and cmd[:2] == ["git", "branch"] and "--format=%(refname:short)" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=branches_output, stderr="")
        # gh issue view N --json state -q .state
        if isinstance(cmd, list) and cmd[:3] == ["gh", "issue", "view"]:
            n = int(cmd[3])
            state = issue_states.get(n)
            if state is None:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=state, stderr="")
        # git branch -D <branch>
        if isinstance(cmd, list) and cmd[:3] == ["git", "branch", "-D"]:
            deleted_branches.append(cmd[3])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # Default: success no-op (clone/fetch/checkout/pull/worktree-prune)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    dispatch.deleted_branches = deleted_branches  # type: ignore[attr-defined]
    return dispatch


def test_prune_keeps_branch_with_no_issue_numbers(tmp_path, monkeypatch):
    """A branch whose name parses to no issue numbers must be preserved."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\nfeat/no-numbers-here\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feat/no-numbers-here" not in dispatch.deleted_branches, (
        f"Branch with no issue numbers must be preserved; deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_integration_branch_explicitly(tmp_path, monkeypatch):
    """`claude-agent-station` must never be deleted regardless of issue states."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "claude-agent-station" not in dispatch.deleted_branches


def test_prune_keeps_base_branch_explicitly(tmp_path, monkeypatch):
    """The project's base branch (e.g. `main`) must never be deleted."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "main" not in dispatch.deleted_branches


def test_prune_deletes_branch_when_all_referenced_issues_closed(tmp_path, monkeypatch):
    """Branch `feature/backend-issues-29-30-...` where both #29 and #30 are CLOSED must be deleted."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "feature/backend-issues-29-30-20260519T080446Z\n"
    )
    issue_states = {29: "CLOSED", 30: "CLOSED"}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feature/backend-issues-29-30-20260519T080446Z" in dispatch.deleted_branches, (
        f"Branch with all CLOSED issues should be deleted; deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_branch_when_any_referenced_issue_still_open(tmp_path, monkeypatch):
    """Branch `feature/backend-issues-29-61-...` where #29 is CLOSED but #61 is OPEN must be preserved."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "feature/backend-issues-29-61-20260519T080446Z\n"
    )
    issue_states = {29: "CLOSED", 61: "OPEN"}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feature/backend-issues-29-61-20260519T080446Z" not in dispatch.deleted_branches, (
        f"Branch with at least one OPEN referenced issue must be preserved; "
        f"deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_branch_when_github_query_fails(tmp_path, monkeypatch):
    """If `gh issue view N` fails for a branch's issue, the branch must be preserved (fail-soft + conservative)."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "autonomous/issue-99\n"
    )
    # issue 99 is intentionally NOT in the cache → query "fails"
    issue_states = {}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "autonomous/issue-99" not in dispatch.deleted_branches, (
        f"Branch must be preserved when issue state query fails; deleted={dispatch.deleted_branches}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_workspace_setup.py -xvs -k "prune"`
Expected: 6 FAIL — `_prune_stale_branches` doesn't exist yet, and `ensure_workspace` never calls `git branch -l` or any of the new commands. The new tests' assertions about deletion behavior won't fire because nothing's deleting anything.

(Note: the tests assert that specific branches are NOT in `deleted_branches`. With no cleanup at all, that assertion is trivially satisfied. So the failure mode for SOME tests will be "vacuously passes" rather than fail. Specifically `test_prune_deletes_branch_when_all_referenced_issues_closed` WILL fail because it requires a branch to be deleted. The other 5 will vacuously pass pre-implementation. This is acceptable — the deletion test is the load-bearing one.)

If you want stricter pre-fail behavior, add to each "keeps" test:
```python
assert dispatch.call_args_list  # at least confirm subprocess was called
```
But this isn't required for the plan.

- [ ] **Step 3: Add the `_prune_stale_branches` helper**

Edit `agent/workspace_setup.py`. Add the helper as a module-level function, BEFORE `ensure_workspace` (so the import is visible at function definition time). Specifically, insert immediately after the `_slug(name)` helper definition (around line 47):

```python
def _prune_stale_branches(
    workspace: Path,
    project_repo: str,
    base_branch: str,
    env: dict[str, str] | None = None,
) -> None:
    """Delete local branches whose referenced GitHub issues are all CLOSED.

    Best-effort. Failures (git command failures, gh query errors) are
    logged at WARNING and the function continues. The integration
    branch (``claude-agent-station``) and ``base_branch`` are NEVER
    deleted, regardless of their names.

    Issue numbers are parsed from branch names via the regex defined
    in :data:`agent.verdict_execution._BRANCH_ISSUES_RE` (added by
    PR #461) — single source of truth across the codebase.

    Conservative: when an issue's state can't be determined, branches
    referencing it are preserved. Only deletes when ALL referenced
    issues are confirmed CLOSED.

    #462.
    """
    from agent.verdict_execution import _BRANCH_ISSUES_RE  # noqa: PLC0415 — lazy import

    PRESERVED = {"claude-agent-station", base_branch}

    # Step 1: list local branches.
    list_result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if list_result.returncode != 0:
        logger.warning("prune: git branch list failed: %s",
                       list_result.stderr.strip())
        return
    branches = [b.strip() for b in list_result.stdout.splitlines() if b.strip()]

    # Step 2: parse issue numbers per branch (skipping preserved names).
    branch_issues: dict[str, list[int]] = {}
    for b in branches:
        if b in PRESERVED:
            continue
        numbers: set[int] = set()
        for match in _BRANCH_ISSUES_RE.finditer(b):
            multi = match.group(1)
            single = match.group(2)
            if multi:
                for chunk in multi.split("-"):
                    if chunk.isdigit():
                        numbers.add(int(chunk))
            elif single and single.isdigit():
                numbers.add(int(single))
        if numbers:
            branch_issues[b] = sorted(numbers)

    if not branch_issues:
        return

    # Step 3: query unique issues once, build cache.
    all_numbers = {n for nums in branch_issues.values() for n in nums}
    issue_states: dict[int, str | None] = {}
    for n in sorted(all_numbers):
        result = subprocess.run(
            ["gh", "issue", "view", str(n), "--repo", project_repo,
             "--json", "state", "-q", ".state"],
            cwd=str(workspace), capture_output=True, text=True, env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            issue_states[n] = result.stdout.strip()
        else:
            issue_states[n] = None  # unknown — branch will be preserved
            logger.warning("prune: gh issue view %s failed: %s",
                           n, result.stderr.strip()[:200])

    # Step 4: delete branches where ALL referenced issues are CLOSED.
    for branch, numbers in branch_issues.items():
        states = [issue_states.get(n) for n in numbers]
        if all(s == "CLOSED" for s in states):
            del_result = subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(workspace), capture_output=True, text=True, env=env,
            )
            if del_result.returncode == 0:
                logger.info("prune: deleted stale branch %s (issues %s all CLOSED)",
                            branch, numbers)
            else:
                logger.warning("prune: git branch -D %s failed: %s",
                               branch, del_result.stderr.strip()[:200])
```

- [ ] **Step 4: Wire the helper into `ensure_workspace`**

Edit `agent/workspace_setup.py`. Find the existing `git worktree prune` block in `ensure_workspace` (around lines 97-101):

```python
    # Prune stale worktrees from prior runs.
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(workspace), capture_output=True, text=True,
    )
    return str(workspace)
```

Replace with:

```python
    # Prune stale worktrees from prior runs.
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(workspace), capture_output=True, text=True,
    )

    # #462: prune stale local branches referencing closed issues.
    # Fail-soft — never blocks the run.
    try:
        _prune_stale_branches(workspace, repo, base, env=None)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.exception("prune: _prune_stale_branches failed (non-fatal)")

    return str(workspace)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_workspace_setup.py -xvs -k "prune"`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the broader workspace-setup suite**

Run: `cd dashboard/backend && python3 -m pytest tests/test_workspace_setup.py -x 2>&1 | tail -5`
Expected: all PASS (existing tests + 6 new). The existing tests use `MagicMock(side_effect=_git_ok)` which returns a no-op success for EVERY subprocess call including the new `git branch` and `gh issue view` calls — so the new helper runs but finds no branches to process (empty `git branch` output → early return). Pre-existing tests should pass unchanged.

If any pre-existing test fails because the new helper introduces additional subprocess calls it expects to see in a specific order, fix the test minimally (e.g., update `assert any("clone" in str(c) for c in calls)` is unaffected — it just checks for the substring "clone" anywhere in the call list).

- [ ] **Step 7: Commit**

```bash
git add agent/workspace_setup.py dashboard/backend/tests/test_workspace_setup.py
git commit -m "$(cat <<'EOF'
feat(workspace_setup): prune local branches with all-closed issues at setup (#462)

Adds _prune_stale_branches helper to agent/workspace_setup.py. Called
from ensure_workspace after the existing git worktree prune. Parses
issue numbers from branch names via _BRANCH_ISSUES_RE (single source
of truth from PR #461), queries gh for each unique issue's state,
and deletes branches where ALL referenced issues are CLOSED.

Always preserves the integration branch (claude-agent-station) and
the project's base branch (e.g. main). Conservative on unknown state:
when a gh query fails, the referencing branches are preserved.

Fail-soft: outer try/except in ensure_workspace catches any exception
and logs at WARNING; the run continues. Branch-list failures, gh query
failures, and individual delete failures are each logged independently
and never block the cleanup pass for other branches.

This unblocks the autonomous loop's issue picker from being
indefinitely waylaid by stale branches referencing closed issues —
the symptom observed across run-20260519T142712Z, 145108Z, and
161640Z on next-itsm, where 125 local branches accumulated and the
lead kept re-evaluating them instead of picking up newly-filed
issues.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Lead-prompt tightening — Picker rules section

**Files:**
- Modify: `agent/station_orchestrator.py`
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
def test_build_team_prompt_includes_picker_rules():
    """The picker-rules section (#462) must appear in the prompt for
    non-plan_only modes."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 99, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        project_mode="full",
    )
    assert "BEFORE assigning any work" in prompt
    assert "workspace_setup automatically prunes" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py::test_build_team_prompt_includes_picker_rules -xvs`
Expected: FAIL with `AssertionError` — the strings are not in the prompt yet.

- [ ] **Step 3: Add the picker-rules section to `build_team_prompt`**

Edit `agent/station_orchestrator.py`. Find the existing READ FIRST instruction block in `build_team_prompt` (around line 1096-1107). The current block is the teammate-spawn template:

```python
When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else.
READ FIRST: `.claude-team-contracts.md` in your worktree root. It documents the cross-team
contracts (field names, route ownership, response shapes, enum values) you MUST follow.
Manager review will reject any branch that violates the contract.

For the QA teammate specifically: when writing or modifying tests,
every assertion on an API response field MUST match the contract's
Response Shapes section. If your test expects a field name that
isn't in the contract, the test is wrong — fix the test to match
the contract. Never change source files (routes, services, models)
to match what your test happened to assert."
```

Immediately AFTER this block (after the closing `"`), insert a NEW section:

```python
## Picker rules (#462)

BEFORE assigning any work, you MUST call pick_issue against fresh
GitHub state. If you find local branches in the workspace, IGNORE
them — workspace_setup automatically prunes branches whose issues
have been closed. Any remaining local branches are for currently-OPEN
issues only, and even those you should NOT resume unless pick_issue
explicitly selected the corresponding issue this run.
```

Verify that this new text appears in the returned prompt (it's literal text inside the f-string, like the existing instructions).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py::test_build_team_prompt_includes_picker_rules -xvs`
Expected: PASS.

- [ ] **Step 5: Run broader prompt-builder tests to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py -x 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): add Picker rules section to lead prompt (#462)

build_team_prompt gains an explicit "Picker rules" block telling the
lead: BEFORE assigning work, call pick_issue against fresh GitHub
state. IGNORE local branches — workspace_setup prunes the stale ones,
and the remaining ones must not be resumed without explicit picker
selection.

Pairs with the stale-branch cleanup in workspace_setup (Task 1) to
close the picker-bypass gap observed on run-20260519T161640Z, where
the lead kept re-evaluating branches for already-closed issues
instead of picking up freshly-filed work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Locate a sensible section**

Run: `grep -n "workspace\|ensure_workspace\|Workspaces\|Workspace setup" docs/architecture.md | head -10`

Expected: at least one match. If no dedicated workspace section exists, the verdict-execution section (recently extended by PR #461) is the next-best location since the new pruning behavior intersects with the issue-state semantics.

- [ ] **Step 2: Add the pruning note**

Insert this paragraph into the appropriate location (a workspace-setup section if one exists, otherwise create a brief subsection):

```markdown
**Stale-branch pruning (#462):** At workspace setup, local branches
whose referenced GitHub issues are all CLOSED are automatically
deleted. The integration branch (`claude-agent-station`) and the
project's base branch are always preserved. Failures (network,
permission) are logged at WARNING and never block the run — and the
pruner is conservative: if any issue's state can't be determined,
the referencing branch is preserved. Issue numbers are parsed from
branch names via the same regex used by the auto-close path
(`_BRANCH_ISSUES_RE` in `agent/verdict_execution.py`).
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): document stale-branch pruning at workspace setup (#462)

Brief note describing the new pruning behavior: closed-issue branches
deleted, integration + base preserved, fail-soft, conservative on
unknown state. Keeps docs in lockstep with the implementation per
CLAUDE.md.

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
cd dashboard/backend && python3 -m pytest \
  tests/test_workspace_setup.py \
  tests/test_orchestrator_wiring.py \
  -xvs 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 2: Broader backend sweep**

Run:

```bash
cd dashboard/backend && python3 -m pytest tests/ \
  --ignore=tests/test_database.py \
  --ignore=tests/test_migration_script.py \
  --ignore=tests/test_pubsub.py \
  -x 2>&1 | tail -5
```

Expected: ≥ 1512 passed (the count from PR #461's sweep), 1 skipped. New tests bump the count by 7 (6 prune + 1 picker-rules).

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
gh pr create --base dev --title "feat: prune stale branches at workspace setup + picker-first lead prompt (#462)" --body "$(cat <<'EOF'
## Summary

Closes #462. When the autonomous loop ran out of fresh issues to pick up, the lead kept re-evaluating stale local branches from prior runs (some 125 deep in the \`next-itsm\` workspace) instead of querying GitHub for newly-filed open issues. This PR adds two complementary fixes:

1. **Workspace cleanup** — \`agent/workspace_setup.py\` now deletes local branches whose referenced GitHub issues are ALL closed, at every workspace setup. Always preserves integration + base branches.
2. **Lead-prompt tightening** — \`build_team_prompt\` gains a \`Picker rules (#462)\` section instructing the lead to call \`pick_issue\` against fresh GitHub state and IGNORE local branches.

This unblocks the auto-close verification path from PR #461 — and any other feature that depends on the picker reaching new work.

## Spec & plan

- Spec: \`docs/superpowers/specs/2026-05-19-stale-branch-cleanup-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-19-stale-branch-cleanup.md\`

## Changes by file

- \`agent/workspace_setup.py\` — new \`_prune_stale_branches(workspace, repo, base_branch, env)\` helper. Called from \`ensure_workspace\` after \`git worktree prune\`, wrapped in fail-soft \`try/except\`. Reuses \`_BRANCH_ISSUES_RE\` from PR #461 (single source of truth for branch-name parsing).
- \`agent/station_orchestrator.py\` — \`build_team_prompt\` gains a \`Picker rules\` block.
- \`docs/architecture.md\` — brief note about the new pruning behavior.

## Defenses

- **Conservative** — only deletes when ALL referenced issues are confirmed CLOSED. Any unknown state → preserves.
- **Fail-soft** — \`gh issue view\` failures preserve, \`git branch -D\` failures logged + continue, outer \`try/except\` in \`ensure_workspace\` catches anything unexpected. Cleanup never blocks the run.
- **Cache** — unique issues queried once per pass (not once per branch). With ~15 unique issues across 125 stale branches, that's ~10s of GitHub queries vs ~60s without caching.
- **Always preserves** \`claude-agent-station\` and the project's base branch (regardless of name).

## Tests

- 6 new unit tests in \`test_workspace_setup.py\`: no-numbers preserved, integration preserved, base preserved, all-closed deleted, any-open preserved, query-failure preserved.
- 1 new snapshot test in \`test_orchestrator_wiring.py\` for the picker-rules block.
- Focused: ~117 pass. Broader sweep: ≥1519 passed, 1 skipped.

## Smoke test (post-merge, NOT in CI)

1. Rebuild containers (\`docker compose build dashboard agent && docker compose up -d\`).
2. Inspect the \`next-itsm\` workspace branch count BEFORE: \`docker exec cas-agent sh -c 'cd /var/lib/claude-agent-station/workspaces/next-itsm && git branch -l | wc -l'\` (expected: large — 125+ from prior runs).
3. Trigger a run.
4. After workspace setup, re-inspect branch count (expected: ~3-5 — only the integration branch, \`main\`, and any open-issue branches).
5. Confirm the lead picks up issue #61 (the open verification target) instead of replaying stale closed-issue branches.
6. **Closes the PR #461 verification loop**: with #61 picked, an APPROVE verdict should trigger \`gh issue close 61\` per the PR #461 auto-close logic. Confirm: \`gh issue view 61 --repo laboef1900/next-itsm --json state -q .state\` returns \`CLOSED\`.

## Closes

Closes #462

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge — rebuild and smoke**

```bash
git fetch origin && git reset --hard origin/dev
docker compose build dashboard agent && docker compose up -d --force-recreate dashboard agent
```

Check baseline branch count:

```bash
docker exec cas-agent sh -c 'cd /var/lib/claude-agent-station/workspaces/next-itsm && git branch -l | wc -l'
```

Trigger:

```bash
API_KEY=$(grep '^STATION_API_KEY=' .env | cut -d= -f2)
curl -s -X POST http://localhost:8420/api/runs/trigger \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json'
```

After the run completes:

```bash
# Confirm branch count dropped
docker exec cas-agent sh -c 'cd /var/lib/claude-agent-station/workspaces/next-itsm && git branch -l | wc -l'
```

Expected: a single-digit number (just integration + base + any branches for issues still OPEN).

For the auto-close verification: confirm issue #61 (still OPEN at PR #462 merge time) is now CLOSED:

```bash
gh issue view 61 --repo laboef1900/next-itsm --json state,closedAt -q '"state=" + .state + " closedAt=" + (.closedAt // "null")'
```

Expected: `state=CLOSED closedAt=<recent timestamp>` if the run produced an APPROVE verdict on #61. Otherwise `state=OPEN closedAt=null` is also acceptable — it means the run picked a different open issue, which itself is evidence the picker is no longer stuck on stale branches.

If all checks pass, close #462:

```bash
MERGE_COMMIT=$(gh pr view <PR-NUMBER> --json mergeCommit -q .mergeCommit.oid | cut -c1-10)
gh issue close 462 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via live smoke run."
```

---

## Self-Review

**Spec coverage:**

- New `_prune_stale_branches` helper ✅ Task 1.
- Called from `ensure_workspace` after `git worktree prune`, fail-soft ✅ Task 1 Step 4.
- Preserves integration + base branch always ✅ Task 1 tests 2, 3.
- Deletes branches where ALL referenced issues CLOSED ✅ Task 1 test 4.
- Preserves branches where any issue still OPEN ✅ Task 1 test 5.
- Preserves on gh query failure ✅ Task 1 test 6.
- Cache via unique-issue lookup (`sorted(all_numbers)`) ✅ Task 1 Step 3 implementation.
- Reuses `_BRANCH_ISSUES_RE` from PR #461 ✅ Task 1 lazy import.
- Lead-prompt `Picker rules` section ✅ Task 2.
- Docs update ✅ Task 3.
- Live verification ties to PR #461's auto-close path ✅ Task 5 Step 3.

**Placeholder scan:** No TBD/TODO. All code blocks complete. Commands have expected output. Test fixtures define their own helper (`_build_git_branch_dispatch`) so the test code is self-contained.

**Type consistency:**
- `_prune_stale_branches(workspace: Path, project_repo: str, base_branch: str, env: dict | None) -> None` matches across Task 1 implementation and Task 1 Step 4 call site.
- `_BRANCH_ISSUES_RE` imported lazily inside the helper — matches the import location pattern from PR #461 where it's also used inside other functions.
- `PRESERVED = {"claude-agent-station", base_branch}` is a set literal containing two strings.

**One subtlety worth flagging for the implementer:**

The `_build_git_branch_dispatch` test helper is fairly intricate. It must dispatch on the EXACT shape of subprocess.run's command list. The implementer should:
1. Read the existing `_git_ok` helper at the top of `test_workspace_setup.py` for the existing pattern.
2. Confirm `_build_git_branch_dispatch` slots in cleanly — it's a strict superset of `_git_ok` (handles all the existing commands as default no-op success).
3. The helper attaches `deleted_branches` to itself via `dispatch.deleted_branches = []` — Python lets you attach attributes to functions; this is a common pytest mock pattern. If lint flags it, add `# type: ignore[attr-defined]` (already in the snippet).

Self-review clean.
