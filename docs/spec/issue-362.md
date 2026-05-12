# Spec — Issue #362: Port project loop iteration to Python

**Status**: spec
**Design**: [`docs/design/milestone-2.md`](../design/milestone-2.md)
**Issue**: [#362](https://github.com/kenhaesler/claude-agent-station/issues/362)
**Targets**: `dev`
**Depends on**: #361

## Acceptance (from issue)

- [ ] `agent/project_loop.py::_pick_issue` selects the same issue the bash would have, given the same project state.
- [ ] The orchestrator dispatch is a direct Python call; no `subprocess.run` for that path.
- [ ] Existing bash blocks for issue picking + dispatch are deleted from `run-manager.sh`.

## Files changed

| Path | Action | Approx LOC |
|---|---|---|
| `agent/project_loop.py` | rewrite from shim to native impl | ~+200 |
| `agent/gh_client.py` | new — `gh` subprocess + JSON helper | ~+80 |
| `agent/scripts/run-manager.sh` | delete picking + dispatch blocks (lines ~1900–2100, ~2680, ~2757) | ~−400 |
| `tests/test_project_loop_pick_issue.py` | new | ~+120 |
| `tests/test_project_loop_dispatch.py` | new | ~+100 |
| `docs/architecture.md` | update flow diagram | small |
| `docs/configuration.md` | update env vars referenced by the loop | small |

## Implementation steps

### 1. `agent/gh_client.py` (helper)

A thin wrapper to centralise `gh` invocations:

```python
from dataclasses import dataclass
import subprocess, json, shlex, logging

logger = logging.getLogger(__name__)

@dataclass
class GhError(Exception):
    cmd: list[str]
    returncode: int
    stderr: str

def gh_json(args: list[str], *, env: dict | None = None, timeout: int = 30) -> Any:
    """Run `gh ARGS` and parse stdout as JSON. Raises GhError on non-zero exit."""
    cmd = ["gh", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode != 0:
        raise GhError(cmd, result.returncode, result.stderr)
    return json.loads(result.stdout)

def gh_run(args: list[str], *, env: dict | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run `gh ARGS` non-JSON. Returns CompletedProcess; caller checks returncode."""
    return subprocess.run(["gh", *args], capture_output=True, text=True, env=env, timeout=timeout)
```

### 2. `_pick_issue` port

Read the existing bash logic in `run-manager.sh` lines ~1900–2100 to extract the filter rules. Expected filters (verify against bash before implementing):

- Repository = `project.repo`
- `state="open"`
- Skip issues with label `backlog`
- Skip issues already claimed by an in-flight coordinator task
- Skip issues with label matching project's `skip_labels` config (if present)
- Prefer oldest open issue (lowest issue number? or oldest createdAt?) — bash defines the tie-break; match it exactly

```python
def _pick_issue(project: Project, *, env: dict | None = None) -> IssueDesc | None:
    args = ["issue", "list",
            "--repo", project.repo,
            "--state", "open",
            "--limit", "100",
            "--json", "number,title,labels,createdAt"]
    issues = gh_json(args, env=env)

    def eligible(i: dict) -> bool:
        labels = {l["name"] for l in i.get("labels", [])}
        if "backlog" in labels: return False
        if labels & set(project.skip_labels or []): return False
        if _is_claimed(project.repo, i["number"]): return False
        return True

    eligible_issues = [i for i in issues if eligible(i)]
    if not eligible_issues:
        return None
    chosen = min(eligible_issues, key=lambda i: i["number"])  # ← confirm tie-break vs bash
    return IssueDesc(number=chosen["number"], title=chosen["title"], labels=[l["name"] for l in chosen["labels"]])
```

`_is_claimed` queries the dashboard's coordinator API (or DB directly) for in-flight tasks on that repo+issue. Reuse `agent/coordinator_lifecycle.py` if a helper exists; add one if not.

### 3. In-process dispatch

After `_pick_issue` returns an issue, call the orchestrator directly instead of shelling. The existing `station_orchestrator.py::orchestrate` coroutine is the target:

```python
from agent.station_orchestrator import orchestrate, load_config

async def _dispatch(config_path: str, run_id: str, workspaces_dir: str, issue: IssueDesc) -> int:
    # The orchestrator currently iterates all enabled projects; we want it to
    # handle a single project+issue. Spec change: orchestrate() gains an
    # optional `single_project` arg (or a new `dispatch_one()` entrypoint).
    config = load_config(config_path)
    return await orchestrate(config, run_id, workspaces_dir, target_issue=issue)

def iterate_projects(run_id: str, config_path: str, workspaces_dir: str) -> int:
    config = load_config(config_path)
    overall = 0
    for project in config.projects:
        if not project.enabled: continue
        issue = _pick_issue(project)
        if issue is None: continue
        rc = asyncio.run(_dispatch(config_path, run_id, workspaces_dir, issue))
        overall = max(overall, rc)
    return overall
```

Concurrency: where the bash used `&`, wrap the project iteration in `concurrent.futures.ThreadPoolExecutor(max_workers=config.limits.max_concurrent_employees)`. Synchronous from the caller's perspective; parallel internally.

### 4. Delete bash

Remove from `run-manager.sh`:
- Lines ~1900–2100: issue picking + label filtering
- Lines ~2680: the `run_start` webhook now fires from `RunDriver` (#361)
- Lines ~2757: the orchestrator subprocess invocation
- Anything in between that becomes dead after the above

Verify with `bash -n run-manager.sh` after deletion. Run the bats/shell tests if any remain.

### 5. Tests

- `test_project_loop_pick_issue.py`:
  - Fixture: mock `gh_json` to return synthetic issue lists. Cases:
    - Empty list → returns None.
    - All have `backlog` → returns None.
    - One eligible → returns it.
    - Multiple eligible → returns lowest number (or whichever tie-break matches bash).
    - Mixed with claimed issues → claimed are filtered.
- `test_project_loop_dispatch.py`:
  - Mock `orchestrate` to a fast no-op; assert `iterate_projects` calls it once per enabled project that has an eligible issue.
  - Mock a project with no eligible issue; assert skipped without calling `orchestrate`.

### 6. Documentation

`docs/architecture.md`: update the orchestration flow section. Remove references to bash issue picking; add `agent/project_loop.py::_pick_issue` and the in-process orchestrate call.

## Risks

- **Filter parity.** The bash's `gh issue list` filter is the source of truth — read it carefully (line 1900–2100) and replicate exactly. Differences in tie-break ordering or skip-label semantics can cause issues to be picked up or skipped wrong, with no test signal.
- **`asyncio.run` inside a thread pool.** Each worker thread creating its own event loop is fine, but pay attention to global state (loggers, env). Run a smoke test with `max_concurrent_employees=3`.
- **Coordinator-task claim race.** Between `_pick_issue` filtering claimed issues and `orchestrate` actually starting, another run could claim the same issue. Mitigation: `coordinator_lifecycle.claim_task` should be the source of truth — `_pick_issue` is best-effort; `claim_task` is atomic. Document this in the module docstring.

## Rollback

Revert the PR. The bash blocks come back. `project_loop.py` reverts to the shim. `RunDriver` still calls `iterate_projects`, which goes back to invoking bash. Clean rollback.

## Out of scope

- Verdict execution (#363).
- Async-everywhere conversion.
- Removing `run-manager.sh` entirely (that happens in #363 if it's the last user of the file).
