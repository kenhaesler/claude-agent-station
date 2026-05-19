# Auto-Close GitHub Issues on APPROVE — Design

**Issue:** Closes #460
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-19
**Target branch:** PR targets `dev` (per project policy)

## Problem

The agent station completes work on GitHub issues, opens per-teammate feature branches, has the manager review them, gets `APPROVE` / `APPROVE_INTEGRATION` verdicts, and merges those branches into the project's `claude-agent-station` integration branch. But the GitHub issues stay OPEN, because:

- Per-teammate PRs target the integration branch (`claude-agent-station`), not the default branch (`main`).
- GitHub's auto-close on `Closes #N` PR footers only fires when the PR merges into the default branch.
- The agent station's verdict-execution flow stops at the integration-branch merge; promotion to `main` and issue closure are left as a manual human step that nobody does.

Live evidence from `laboef1900/next-itsm`: 6 merged PRs (#50–#55) addressed issues #29 and #30 across two runs of the autonomous loop, yet both issues remained OPEN. The integration branch is 23 commits ahead of `main` with 0 behind. The work is genuinely done; only the close step is missing.

A second data point came from inspecting real verdicts files: across the last three runs, all three teammate branches named `feature/{role}-issues-29-30-...` were emitted with `verdict.issue_number=30` only — issue #29 would never close from the verdict field alone. One verdict had `issue_number=None` entirely. **The branch name is a more reliable source of issue numbers than the verdict field.**

## Goals

- When verdict execution merges work onto the integration branch with an `APPROVE` or `APPROVE_INTEGRATION` verdict, close the GitHub issue(s) the work addresses.
- Use the union of `verdict.issue_number` and branch-name-extracted issue numbers (dedup), so multi-issue branches close every issue they cover.
- Idempotent via `gh` semantics — no run-level state, no in-memory dedup. `gh issue close` on an already-closed issue is logged at WARNING and ignored.
- Fail-soft: a failing close never crashes a verdict or run.

## Non-Goals

- Auto-promotion of `claude-agent-station` → `main`. The integration branch is intentionally a human-gated review point.
- Closing on `PR` verdicts (draft PR — not done yet, issue stays open by design).
- Reopening issues if a run is later interrupted or reaped.
- Closing on `REJECT` / `SKIP` verdicts.
- Changing the `Verdict` dataclass schema. Multi-issue support is derived at executor time, not stored.

## High-Level Architecture

One new pure utility + one new helper + two call sites — all in `agent/verdict_execution.py`.

### Components

**1. `_BRANCH_ISSUES_RE` (module-level regex)**

Captures both branch naming conventions seen in the wild:

```python
_BRANCH_ISSUES_RE = re.compile(r"\bissues?-(\d+(?:-\d+)*)\b|/issue-(\d+)\b")
```

- Matches `feature/backend-issues-29-30-20260519T...` → captures `"29-30"` in group 1.
- Matches `autonomous/issue-31` → captures `"31"` in group 2.

### `_resolve_issue_numbers(verdict: Verdict) -> list[int]`

Pure function. Returns the union of `verdict.issue_number` (if set) and every number found in the branch name via the regex above. Deduplicated and sorted ascending. Returns `[]` if no issues found.

Behavior table:

| `verdict.branch` | `verdict.issue_number` | Returns |
|---|---|---|
| `feature/backend-issues-29-30-20260519T...` | `30` | `[29, 30]` |
| `feature/backend-issues-29-30-20260519T...` | `None` | `[29, 30]` |
| `autonomous/issue-31` | `31` | `[31]` (deduped) |
| `feature/no-numbers` | `42` | `[42]` |
| `feature/no-numbers` | `None` | `[]` |

### `_close_issues(verdict, *, pr_url, run_id, env, into) -> None`

New helper that mirrors the existing `_post_issue_comment` pattern (same file, line 437). For each resolved issue number, calls:

```bash
gh issue close <N> --repo <project> --reason completed \
  --comment "Closed by autonomous run <run_id> via PR <pr_url>."
```

Each `gh_run` call is wrapped — failures (already-closed, permission error, etc.) are logged at WARNING and the helper continues to the next issue. The outer verdict's success state is never affected.

Body fragments are conditionally included: if `run_id` is None or `pr_url` is empty, those parts are omitted. The closing comment is always short (one sentence + the verdict's reasoning is NOT duplicated — it already appears in the prior `_post_issue_comment` call).

### Call site changes (2)

**`execute_approve`** (around line 180): after the existing `_post_issue_comment(...)` call, add:

```python
if result.pr_url:
    _close_issues(verdict, pr_url=result.pr_url, run_id=run_id, env=env, into=result)
```

**`execute_approve_integration`** (around line 395): same call shape after the `_post_issue_comment(...)` call.

NOT added to `execute_pr` (draft PR), `execute_reject`, `execute_skip`. Per the spec.

## Data Flow

```
execute_approve / execute_approve_integration
 ├─► push branch (existing)
 ├─► gh pr create (existing) → result.pr_url
 ├─► [APPROVE_INTEGRATION only] gh pr merge --auto --squash (existing)
 ├─► _post_issue_comment (existing — adds the verdict comment)
 └─► NEW: _close_issues(verdict, pr_url=result.pr_url, run_id, env, result)
       ├─► _resolve_issue_numbers(verdict) → [N1, N2, ...]
       └─► for each N: gh issue close N --repo PROJECT --reason completed --comment "..."
            └─► failure → logger.warning + continue
```

No changes to `iterate_projects`, no new run-level state, no signature changes on the public dispatcher.

## Error Handling

| Condition | Behavior |
|---|---|
| `verdict.branch` doesn't match either regex AND `verdict.issue_number is None` | `_resolve_issue_numbers` returns `[]`; `_close_issues` is a no-op. |
| `gh issue close` returns non-zero (already closed, permission denied, network) | `logger.warning(...)`, continue to next issue. Verdict success unchanged. |
| `result.pr_url` is empty (e.g. `gh pr create` succeeded silently with no output) | Guard at the call site: `if result.pr_url: _close_issues(...)`. Skip the close if no PR URL — prevents an empty link in the comment. |
| Multi-issue branch where some issues are already closed | `gh issue close` returns error on the already-closed ones; logged at WARNING; the still-open ones are closed normally. |

## Testing

### Unit tests in `dashboard/backend/tests/test_verdict_execution.py` (or whichever existing file holds verdict-execution tests — verify during implementation; create new file if absent)

1. **`test_resolve_issue_numbers_from_multi_issue_branch`** — branch `feature/backend-issues-29-30-20260519T080446Z`, verdict.issue_number=30 → `[29, 30]`.
2. **`test_resolve_issue_numbers_from_old_convention`** — branch `autonomous/issue-31`, verdict.issue_number=31 → `[31]` (dedup).
3. **`test_resolve_issue_numbers_falls_back_to_verdict_only`** — branch with no number pattern, verdict.issue_number=42 → `[42]`.
4. **`test_resolve_issue_numbers_empty_when_no_source`** — branch with no number pattern, verdict.issue_number=None → `[]`.
5. **`test_execute_approve_closes_issue_after_pr_created`** — mock `gh_run`; drive `execute_approve` with APPROVE verdict; assert `gh issue close N --repo X` is called exactly once.
6. **`test_execute_approve_integration_closes_after_merge_armed`** — same shape for APPROVE_INTEGRATION.
7. **`test_close_issues_handles_multi_issue_branch`** — drive `execute_approve` with branch `feature/backend-issues-29-30-...`; assert `gh issue close` called for BOTH 29 AND 30.
8. **`test_execute_reject_does_not_close_issue`** — REJECT verdict; assert no `gh issue close` calls.
9. **`test_execute_skip_does_not_close_issue`** — SKIP verdict; assert no `gh issue close` calls.
10. **`test_close_issues_swallows_gh_failure`** — mock `gh_run` to return `ok=False` on the close call; assert verdict still has `success=True` and a WARNING is logged.

### Integration / smoke test (post-merge, NOT in CI)

Trigger a run on `next-itsm` that produces at least one APPROVE / APPROVE_INTEGRATION verdict on a real open issue. After the run completes, confirm:
- The issue is CLOSED on GitHub (`gh issue view N --repo laboef1900/next-itsm`).
- The close comment links to the PR URL and run_id.
- The verdict's `success=True` was reported (the close didn't break execution).

## Backwards Compatibility

- New behavior is purely additive. Previous runs left issues open; new runs close them. No data migration.
- No signature change to public `execute()` dispatcher.
- No changes to `Verdict` dataclass.
- Existing tests mocking `gh_run` continue to work; new tests assert the additional call.
- The `_close_issues` call is gated on `result.pr_url` being truthy — if a future test mocks PR creation but produces no URL, no close attempt fires. Safe default.

## Acceptance Criteria

- [ ] `_BRANCH_ISSUES_RE`, `_resolve_issue_numbers`, `_close_issues` exist in `agent/verdict_execution.py`.
- [ ] `execute_approve` and `execute_approve_integration` call `_close_issues` after their existing `_post_issue_comment`.
- [ ] `execute_pr`, `execute_reject`, `execute_skip` do NOT call `_close_issues`.
- [ ] All 10 unit tests pass.
- [ ] Broader backend test sweep clean (`dashboard/backend/tests/`).
- [ ] `docs/architecture.md` updated with a brief note in the Verdict execution section.
- [ ] PR targets `dev`. Closes #460.
- [ ] Post-merge live verification on `next-itsm`: a fresh run that APPROVEs an open issue results in that issue being closed automatically on GitHub.

## Out-of-Scope Follow-Ups

- Auto-promotion of `claude-agent-station` → `main` (separate issue).
- Reopening issues if a run is later interrupted or reaped (would require coordinating with the stale-run reaper).
- Closing on `PR` verdict (draft PR — would be inconsistent: the work isn't merged yet).
- A dashboard view of "issues closed by this run" (future polish).
