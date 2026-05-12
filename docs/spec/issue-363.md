# Spec — Issue #363: Port verdict execution path to Python

**Status**: spec
**Design**: [`docs/design/milestone-2.md`](../design/milestone-2.md)
**Issue**: [#363](https://github.com/kenhaesler/claude-agent-station/issues/363)
**Targets**: `dev`
**Depends on**: #361 (may land before or after #362)

## Acceptance (from issue)

- [ ] Verdicts (APPROVE / PR / REJECT) for a triggered run produce the same `gh`/`git` state as the bash path.
- [ ] Conflict resolution still works (current bash conflict resolver is a separate subprocess; keep that interface).
- [ ] Bash verdict blocks deleted from `run-manager.sh`.

## Files changed

| Path | Action | Approx LOC |
|---|---|---|
| `agent/verdict_execution.py` | new | ~+250 |
| `agent/gh_client.py` | extend or create (shared with #362) | small |
| `agent/project_loop.py` | call verdict_execution after manager review | ~+30 |
| `agent/scripts/run-manager.sh` | delete verdict blocks (~lines 2100–2500) | ~−400 |
| `tests/test_verdict_execution.py` | new | ~+200 |
| `docs/architecture.md` | update | small |

## Acceptance breakdown

Each bash verdict path has a Python equivalent that yields **identical observable side effects** (gh API state, git refs, issue comments, labels). Specifically:

| Verdict | Bash actions | Python equivalent |
|---|---|---|
| APPROVE | `git push origin <branch>`; `gh pr create … --base main`; merge if auto-merge; `gh issue comment` summary; close issue | `execute_approve(verdict, branch, base_branch)` — same |
| PR (draft) | `git push`; `gh pr create --draft`; `gh issue comment` with PR URL | `execute_pr(verdict, branch, base_branch, draft=True)` |
| REJECT | `gh issue comment` with rejection reason; `gh issue edit --add-label <reject_label>` | `execute_reject(verdict, reason)` |
| SKIP | `gh issue comment` with skip reason | `execute_skip(verdict, reason)` |

## Implementation steps

### 1. `agent/verdict_execution.py`

```python
"""Execute manager verdicts via gh/git subprocess calls.

Verdict shape (matches what the manager writes to the verdicts JSON):
{
    "project": "owner/repo",
    "issue_number": 123,
    "decision": "APPROVE" | "PR" | "REJECT" | "SKIP",
    "branch": "autonomous/issue-123",
    "base_branch": "dev",
    "summary": "Manager's prose verdict",
    "reason": "If REJECT/SKIP, the reason"
}
"""

from dataclasses import dataclass
from agent.gh_client import gh_run, gh_json, GhError

@dataclass
class ExecutionResult:
    decision: str
    project: str
    issue_number: int
    success: bool
    pr_url: str | None = None
    error: str | None = None

def execute_approve(verdict: Verdict, *, dry_run: bool = False) -> ExecutionResult:
    if dry_run:
        return ExecutionResult(...success=True)
    # git push origin <branch>
    push = subprocess.run(["git", "push", "origin", verdict.branch], cwd=workspace, ...)
    if push.returncode != 0:
        return ExecutionResult(success=False, error=f"git push failed: {push.stderr}")
    # gh pr create
    pr = gh_run(["pr", "create",
                 "--repo", verdict.project,
                 "--head", verdict.branch,
                 "--base", verdict.base_branch,
                 "--title", verdict.title,
                 "--body", verdict.body])
    if pr.returncode != 0:
        return ExecutionResult(success=False, error=...)
    pr_url = pr.stdout.strip()
    # gh issue comment
    gh_run(["issue", "comment", str(verdict.issue_number),
            "--repo", verdict.project, "--body", verdict.summary])
    return ExecutionResult(success=True, pr_url=pr_url, ...)

def execute_pr(...): ...
def execute_reject(...): ...
def execute_skip(...): ...

def execute(verdict: Verdict, **kwargs) -> ExecutionResult:
    """Dispatcher — picks the right execute_* per verdict.decision."""
    return {
        "APPROVE": execute_approve,
        "PR": execute_pr,
        "REJECT": execute_reject,
        "SKIP": execute_skip,
    }[verdict.decision](verdict, **kwargs)
```

### 2. Conflict resolution preservation

The current `agent/conflict_resolver/` runs as a separate subprocess. Before pushing on APPROVE/PR, the bash invokes the conflict resolver if the branch isn't fast-forward-able with `base_branch`. Replicate:

```python
# In execute_approve / execute_pr, before git push:
if _needs_rebase(verdict.branch, verdict.base_branch):
    rc = subprocess.run([sys.executable, "-m", "agent.conflict_resolver",
                         "--branch", verdict.branch,
                         "--base", verdict.base_branch],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        return ExecutionResult(success=False, error=f"conflict_resolver: {rc.stderr}")
```

Keep the subprocess interface — do not refactor conflict resolver in this PR.

### 3. Caller integration

In `agent/project_loop.py` (after #362):

```python
def iterate_projects(...):
    ...
    # After manager review writes verdicts to disk:
    verdicts = _load_verdicts(verdicts_path)
    for v in verdicts:
        result = verdict_execution.execute(v)
        webhook_emitter.emit("verdict_execute", run_id=run_id, payload={...})
```

If #362 hasn't landed yet, `project_loop.py` still shims to bash, but the verdict block in bash now invokes `python3 -m agent.verdict_execution` per verdict instead of running the gh/git calls directly. That intermediate stage is fine — it cleanly retires the bash without depending on #362.

### 4. Delete bash

Remove the verdict execution blocks from `run-manager.sh`. After deletion:
- If #362 has merged: `run-manager.sh` should be near-empty. Delete it if safe.
- If #362 hasn't merged: keep the bash file but with the verdict blocks excised and the dispatcher replaced with `python3 -m agent.verdict_execution`.

### 5. Tests

- `test_verdict_execution.py`:
  - Mock `subprocess.run` for `git push`, `gh pr create`, `gh issue comment`, `gh issue edit`.
  - Per-decision cases: APPROVE happy path, APPROVE with push failure, PR draft happy path, REJECT, SKIP.
  - Conflict resolver branch: subprocess returns non-zero → ExecutionResult.success=False with error wired through.
  - Assert exact argv for each subprocess call (golden-file style) against `tests/fixtures/verdict_argv_<decision>.json`.

### 6. Documentation

`docs/architecture.md`: replace the bash verdict description with the Python module call. List the file paths and the public functions.

## Risks

- **`gh pr create --body` shell quoting.** Multi-line verdict prose with backticks and quotes can corrupt the gh command when bash builds it as a string; Python avoids this naturally via argv lists. Watch for any bash workaround (e.g., writing the body to a temp file and using `--body-file`) — replicate the safe form.
- **Conflict resolver re-entry.** If the resolver itself runs gh/git operations and races against our pre-push gate, results can diverge. Mitigation: pin the resolver invocation to the same workspace as our git push and verify with an integration test.
- **Token scopes.** `gh pr create` needs PR-write scope; `gh issue edit --add-label` needs labels scope. Bash relies on the ambient `GH_TOKEN`; Python should pass the same env. Don't introduce a different auth path.
- **Comment double-posting.** If a verdict execution fails mid-flow (push succeeds, gh pr create fails, retry succeeds), we may double-post the issue comment. Mitigation: include an idempotency marker in the comment body and check `gh issue view` before posting (or accept best-effort — the bash had the same risk).

## Rollback

Revert PR. `verdict_execution.py` becomes dead; bash verdict blocks come back. Clean.

## Out of scope

- Refactoring `agent/conflict_resolver/`.
- Issue picking / dispatch port (#362).
- Removing `run-manager.sh` entirely (incidental if #362 has already landed).
