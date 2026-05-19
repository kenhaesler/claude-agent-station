# Stale-Branch Cleanup + Picker-First Lead Prompt — Design

**Issue:** Closes #462
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-19
**Target branch:** PR targets `dev` (per project policy)

## Problem

The agent station's per-project workspace accumulates local feature branches from prior runs without ever cleaning them up. When a new run triggers, the lead **re-evaluates these stale branches** instead of running the issue picker against fresh GitHub state, even when:

- The issues they reference are CLOSED on GitHub.
- The branches are 0 commits ahead of the integration branch (no new work).
- The branches were forked from a stale base and would delete files added in newer cycles.

Net effect: **newly filed open issues are never picked up.** The autonomous loop gets stuck in a rejection loop on closed-issue branches.

### Live evidence

The `next-itsm` workspace was found to contain **125 local branches** (from May 9 through May 19), most referencing already-closed issues. Three consecutive verification runs (`run-20260519T142712Z`, `run-20260519T145108Z`, `run-20260519T161640Z`) all re-evaluated the same set of stale branches (`feature/backend-issues-29-30-31-v2`, `feature/frontend-issues-29-31-v2`, `feature/qa-issues-29-30-31`) instead of picking up the freshly-filed issue #61.

Manager's reasoning was explicit about the staleness:

> "Issues #29, #30, #31 are all already CLOSED by the repository owner."
> "Branch has 0 new commits ahead of claude-agent-station. The two-dot diff is empty."
> "Branch was created from a stale base ... merging this branch would delete 13 files that were added in prior cycles."

The manager is doing its job correctly. The bug is **upstream**: stale branches are not pruned, and the lead opportunistically resumes them instead of asking GitHub for fresh open issues.

This defect **also blocks verification of PR #461** (auto-close on APPROVE), because the picker never reaches new work for the auto-close path to be exercised end-to-end.

## Goals

- Delete local branches whose corresponding GitHub issues are all CLOSED, automatically at workspace setup.
- Preserve branches whose referenced issues include at least one still-OPEN issue.
- Always preserve the integration branch and the project's base branch, regardless of name.
- Update the lead's spawn prompt to explicitly require the picker before any work assignment, and to ignore local branches.
- Fail-soft: cleanup errors never block the run.

## Non-Goals

- Pruning remote branches on origin (local-only).
- Pruning branches referencing issues in a different repo.
- Aggressive age-based pruning (deferred — issue-state pruning is sufficient for the observed symptom).
- Reaping branches the agent itself opened mid-run (those have a different lifecycle).

## High-Level Architecture

Two complementary changes, both fail-soft:

### A — Branch cleanup at workspace setup

New helper `_prune_stale_branches(workspace_path: Path, project_repo: str, base_branch: str, env: dict | None) -> None` in `agent/workspace_setup.py`. Called from `ensure_workspace` immediately after the existing `git worktree prune` line (`agent/workspace_setup.py:101`).

Algorithm:
1. `git branch -l` → list of local branches in the workspace.
2. For each branch:
   - Skip explicitly-preserved branches: `claude-agent-station` (integration) and `base_branch` (typically `main`).
   - Parse issue numbers from the branch name using **the existing `_BRANCH_ISSUES_RE` from `agent/verdict_execution.py`** (added in PR #461).
   - If no issue numbers parsed, skip (can't safely classify; preserve).
3. Build the set of all unique issue numbers referenced across branches.
4. Query `gh issue view <N> --repo <project> --json state` for each unique issue, building a `{number: state}` cache.
5. For each issue-referencing branch: if **ALL** referenced issues have state `CLOSED`, run `git branch -D <branch>`. If any is still `OPEN` (or query failed for any of them), preserve.
6. Log each deletion at INFO and each preservation/failure decision at DEBUG.

Cache is built once per cleanup pass — 125 stale branches referencing 15-20 unique issues yields ~10 seconds of GitHub queries total, not 125× single-issue lookups.

### B — Lead-prompt tightening

In `agent/station_orchestrator.py::build_team_prompt`, add a `## Picker rules (#462)` section near the existing workflow section. The lead is instructed:

```
## Picker rules (#462)

BEFORE assigning any work, you MUST call pick_issue against fresh
GitHub state. If you find local branches in the workspace, IGNORE
them — workspace_setup automatically prunes branches whose issues
have been closed. Any remaining local branches are for currently-OPEN
issues only, and even those you should NOT resume unless pick_issue
explicitly selected the corresponding issue this run.
```

Unconditional within non-`plan_only` mode (same gating as the existing READ FIRST and contracts-write blocks introduced by PRs #457 and #459).

## Components

### 1. Modified: `agent/workspace_setup.py`

**New helper `_prune_stale_branches`:**

```python
def _prune_stale_branches(
    workspace: Path,
    project_repo: str,
    base_branch: str,
    env: dict[str, str] | None,
) -> None:
    """Delete local branches whose referenced GitHub issues are all CLOSED.

    Best-effort. Failures (git command failures, gh query errors) are
    logged at WARNING and the function continues. The integration
    branch (``claude-agent-station``) and ``base_branch`` are NEVER
    deleted, regardless of their names.

    Issue numbers are parsed from branch names via the regex defined
    in :data:`agent.verdict_execution._BRANCH_ISSUES_RE` (added by
    PR #461) — single source of truth across the codebase.

    #462.
    """
    PRESERVED = {"claude-agent-station", base_branch}

    # Step 1: list local branches.
    list_result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(workspace), capture_output=True, text=True,
    )
    if list_result.returncode != 0:
        logger.warning("prune: git branch list failed: %s",
                       list_result.stderr.strip())
        return
    branches = [b.strip() for b in list_result.stdout.splitlines() if b.strip()]

    # Step 2: parse issue numbers per branch.
    from agent.verdict_execution import _BRANCH_ISSUES_RE
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

    # Step 3: query unique issues once. Cache.
    all_numbers = {n for nums in branch_issues.values() for n in nums}
    issue_states: dict[int, str | None] = {}
    for n in all_numbers:
        result = subprocess.run(
            ["gh", "issue", "view", str(n), "--repo", project_repo,
             "--json", "state", "-q", ".state"],
            cwd=str(workspace), capture_output=True, text=True, env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            issue_states[n] = result.stdout.strip()
        else:
            issue_states[n] = None  # unknown — preserve branches that reference it
            logger.warning("prune: gh issue view %s failed: %s",
                           n, result.stderr.strip()[:200])

    # Step 4: delete branches where ALL referenced issues are CLOSED.
    for branch, numbers in branch_issues.items():
        states = [issue_states.get(n) for n in numbers]
        if all(s == "CLOSED" for s in states):
            del_result = subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(workspace), capture_output=True, text=True,
            )
            if del_result.returncode == 0:
                logger.info("prune: deleted stale branch %s (issues %s all CLOSED)",
                            branch, numbers)
            else:
                logger.warning("prune: git branch -D %s failed: %s",
                               branch, del_result.stderr.strip()[:200])
```

**Wire into `ensure_workspace`:** insert the call AFTER the existing `git worktree prune` and BEFORE `return str(workspace)` (around line 101-102):

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
    except Exception:  # noqa: BLE001
        logger.exception("prune: _prune_stale_branches failed (non-fatal)")

    return str(workspace)
```

Note: `env=None` here — `gh` will use the inherited environment including `GH_TOKEN`. If a future call needs a different token, the parameter is plumbed.

### 2. Modified: `agent/station_orchestrator.py::build_team_prompt`

Add a new section right after the existing READ FIRST block (around `agent/station_orchestrator.py:1100`):

```python
## Picker rules (#462)

BEFORE assigning any work, you MUST call pick_issue against fresh
GitHub state. If you find local branches in the workspace, IGNORE
them — workspace_setup automatically prunes branches whose issues
have been closed. Any remaining local branches are for currently-OPEN
issues only, and even those you should NOT resume unless pick_issue
explicitly selected the corresponding issue this run.
```

The text lands in the lead's spawn prompt verbatim. No code-side guards — the lead is trusted to follow the rule; the workspace cleanup makes the rule self-enforcing for the closed-issue subset.

### 3. New tests in `dashboard/backend/tests/test_workspace_setup.py`

Six new tests covering the helper:

1. **`test_prune_keeps_branch_with_no_issue_numbers`** — branch `feat/no-numbers-here`, no parse → preserved, no GitHub query.
2. **`test_prune_keeps_integration_branch_explicitly`** — branch literally `claude-agent-station` always preserved even if `gh issue view` would return CLOSED for any associated number.
3. **`test_prune_keeps_base_branch_explicitly`** — `main` (the passed-in `base_branch`) always preserved.
4. **`test_prune_deletes_branch_when_all_referenced_issues_closed`** — branch `feature/backend-issues-29-30-...`, mock `gh issue view` to return `CLOSED` for both #29 and #30 → branch deleted.
5. **`test_prune_keeps_branch_when_any_referenced_issue_still_open`** — branch `feature/backend-issues-29-61-...`, #29 CLOSED but #61 OPEN → branch preserved.
6. **`test_prune_keeps_branch_when_github_query_fails`** — mock `gh issue view` to return non-zero → branch preserved, WARNING logged. Fail-soft.

### 4. New snapshot test in `dashboard/backend/tests/test_orchestrator_wiring.py`

**`test_build_team_prompt_includes_picker_rules`** — assert both `"BEFORE assigning any work"` and `"workspace_setup automatically prunes"` are in the prompt returned by `build_team_prompt` for non-`plan_only` modes. One test, two asserts.

### 5. Modified: `docs/architecture.md`

Extend the existing workspace-setup paragraph (or create one if absent) with:

```markdown
**Stale-branch pruning (#462):** At workspace setup, local branches
whose referenced GitHub issues are all CLOSED are automatically
deleted. The integration branch and the project's base branch are
always preserved. Failures (network, permission) are logged at WARNING
and never block the run. Issue numbers are parsed from branch names
via the same regex used by the auto-close path (`_BRANCH_ISSUES_RE`
in `agent/verdict_execution.py`).
```

## Data Flow

```
ensure_workspace(project, workspaces_dir)
 ├─► clone or refresh
 ├─► checkout base branch
 ├─► pull --ff-only
 ├─► git worktree prune   (existing)
 ├─► _prune_stale_branches(workspace, repo, base, env)   (NEW)
 │     ├─► git branch -l                  (collect local branches)
 │     ├─► parse issue numbers per branch (via _BRANCH_ISSUES_RE)
 │     ├─► gh issue view --json state     (per unique issue, cached)
 │     └─► git branch -D                  (per branch where ALL referenced issues are CLOSED)
 └─► return workspace path
```

iterate_projects continues unchanged: it calls `ensure_workspace`, which now hands back a workspace with only relevant branches present. The lead's spawn prompt (B) reinforces the picker-first rule.

## Error Handling

| Condition | Behavior |
|---|---|
| `git branch -l` fails | Log WARNING, skip pruning, continue. |
| `gh issue view N` fails | Mark issue state as unknown for N. Any branch referencing N is preserved (don't delete on incomplete info). WARNING per failure. |
| `git branch -D <name>` fails | Log WARNING, continue with next branch. The leftover branch will be retried on the next run. |
| `_prune_stale_branches` raises unexpectedly | Outer `try/except Exception` in `ensure_workspace` catches and logs via `logger.exception`. Run continues. |
| Issue cache returns mixed (some queries succeeded, some failed) | Branches whose referenced issues are all CLOSED → delete. Any branch with at least one unknown OR open → preserve. |

The cleanup is intentionally **conservative**: it only deletes when it can prove safety. Anything unknown or partially-known is preserved.

## Testing

### Unit tests in `dashboard/backend/tests/test_workspace_setup.py` (6 new)
Listed in Component 3 above. All must pass; pre-existing tests must remain green.

### Snapshot test in `dashboard/backend/tests/test_orchestrator_wiring.py` (1 new)
Listed in Component 4.

### Live verification (post-merge, NOT in CI)
Two-stage:

1. **Cleanup demonstration**: trigger a run on `next-itsm`. Confirm via `docker exec` that the `next-itsm` workspace's branch count drops from ~125 stale branches to a much smaller set (only OPEN-issue branches + `claude-agent-station` + `main`).

2. **End-to-end picker → auto-close**: with issue #61 OPEN and the cleanup having removed all stale issue-29/30/31 branches, the next run should pick #61, produce an APPROVE verdict, and trigger the PR #461 auto-close path that we couldn't verify previously. **Acceptance:** issue #61 is CLOSED on GitHub at run end, with the auto-close comment from PR #461.

## Backwards Compatibility

- `ensure_workspace`'s public contract is unchanged (same signature, same return type, same exception type).
- `_prune_stale_branches` is a new private helper — no callers other than `ensure_workspace`.
- Lead-prompt addition is text-only and gated on non-`plan_only` mode (same gating pattern as #457/#459).
- No DB/schema/wire-format changes.
- Existing workspaces with stale branches will be cleaned on first run after deploy. No migration needed.

## Acceptance

- [ ] `_prune_stale_branches` exists in `agent/workspace_setup.py`.
- [ ] `ensure_workspace` calls it after `git worktree prune`, wrapped in fail-soft `try/except`.
- [ ] Always preserves: `claude-agent-station` and the passed `base_branch`.
- [ ] Deletes branches where all referenced issues are CLOSED.
- [ ] Preserves branches where any referenced issue is still OPEN.
- [ ] Preserves branches when issue-state query fails (fail-soft + conservative).
- [ ] 6 new unit tests + 1 snapshot test pass.
- [ ] `build_team_prompt` includes the picker-rules section.
- [ ] `docs/architecture.md` updated.
- [ ] PR targets `dev`. Closes #462.
- [ ] Post-merge live verification: `next-itsm` workspace branches drop from ~125 to single digits; subsequent run picks #61 and exercises the auto-close path from PR #461.

## Out-of-Scope Follow-Ups

- Remote-branch pruning on origin (would require write permissions to delete on `github.com`).
- Age-based fallback pruning (deferred — current evidence shows issue-state alone is sufficient).
- Branches referencing issues in other repos (cross-repo references — out of scope for V1).
- Auto-promotion of `claude-agent-station` → `main` (separate issue, deferred).
