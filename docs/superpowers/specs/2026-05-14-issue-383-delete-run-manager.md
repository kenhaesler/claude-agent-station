# Delete `run-manager.sh`, Port Phases to Python — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: [#383](https://github.com/kenhaesler/claude-agent-station/issues/383) — Tier 1 / Issue A of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)
**Builds on**: PR #361 / #378 (`RunDriver` production path), PR #362 / #379 (`pick_issue`), PR #363 / #380 (`agent/verdict_execution.py`)
**Depends on**: [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) (`ClaudeSDKClient` migration)

## Context

`agent/scripts/run-manager.sh` is 3309 LOC of bash that cooperates with
`agent/station_orchestrator.py` (2533 LOC Python) through subprocess invocation,
environment variables, temp files, and webhook callbacks. The launcher pipeline
has four hand-offs:

```
launcher.py (Python, FastAPI)
  → station_orchestrator.py main() with --driver (Python)
    → project_loop.py iterate_projects (Python)
      → run-manager.sh --internal-iterate (bash, 3309 LOC)
        → station_orchestrator.py orchestrate (Python, re-entered)
          → claude_agent_sdk → bundled CLI → teammates
```

Three of the four bugs PR #381 patched only existed because of this boundary:

- **Stream-close** hid behind bash re-spawning Python fresh each project iteration.
- **Work-complete-break** hid because the SDK closing stdin acted as an
  incidental rate-limit on the outer loop.
- **Bundled-CLI leak** was harder to spot because the bash log already carried
  noise from `webhook_event` retries, `queue_api` polls, etc.

The bash side owns nine distinct phases. After this issue, **all of them live in
Python**, and `run-manager.sh` either ceases to exist or is reduced to a shim of
≤200 LOC that does nothing but `exec` the Python driver (the issue body keeps
the door open for the latter; the design below assumes full deletion is
achievable, with the shim as a fallback).

The companion script `agent/scripts/integration-branch.sh` (739 LOC) is **not**
deleted by this issue — it is callable by cron, the dashboard, and manually.
Only its `merge_to_dev` function (lines 154–296) is ported into Python.

## Goals

- A run that starts at the dashboard's **Trigger Run** button executes top-to-bottom
  in Python. No `bash run-manager.sh` invocation anywhere in the live path.
- Each former bash phase has a Python module + pytest coverage equivalent to or
  better than its bash predecessor's de-facto behaviour.
- `STATION_LAUNCHER_USE_BASH=1` (the panic-revert flag set up by PR #361) is
  removed from `agent/launcher.py`.
- `agent/project_loop.py::iterate_projects` no longer shells to
  `run-manager.sh --internal-iterate`; the work lives in Python directly.
- The bash EXIT-trap webhook (`_send_run_complete_on_exit`, lines 522–559) is
  obsolete because `RunDriver.run()`'s `try/finally` already owns
  `run_complete` emission.

## Non-goals

- Touching `agent/scripts/integration-branch.sh`, `agent/scripts/promote.sh`,
  `agent/scripts/circuit-breaker.sh`, or `agent/scripts/resolve-conflicts.sh`.
  These remain callable from cron and the dashboard.
- Rewriting `agent/scripts/refresh-token.py` — already Python; gets invoked
  directly from the new `preflight` Python module.
- Backward compatibility with bash callers of `run-manager.sh`. The dashboard
  has not invoked the script directly since #361.
- Changes to webhook payload shape, `run_id` semantics, or dashboard contracts.

## Approach

### Bash phases and their Python destinations

`grep -n '^[a-z_]*()' agent/scripts/run-manager.sh` enumerates 47 functions.
Phase-by-phase port mapping:

| Bash function (line) | Phase | Python destination |
|---|---|---|
| `preflight()` (693) | Preflight, deps, auth check, OAuth refresh | **New** `agent/preflight.py` |
| `ensure_gh_token()` (184) | GH_TOKEN fetch from dashboard | Already in `agent/launcher.py::_fetch_gh_token`; reused. |
| `setup_workspace()` (836) | Clone / refresh / checkout / worktree-prune | **New** `agent/workspace_setup.py` |
| `get_project_count/get_project_field/get_max_*` (783–572) | JSON config reads | Already in `agent/station_orchestrator.py::load_config` + `get_limit`/`get_model`; expose helpers. |
| `check_rate_limit/record_session` (612, 641) | Rate-limit state | **New** `agent/rate_limit.py` (reads the same JSON sidecar bash writes today) |
| `webhook_event/build_webhook_json/queue_api` (300/251/343) | Webhook + queue HTTP | Already in `agent/webhook_emitter.py` + `dashboard/backend/app/routers/queue.py`; ensure call sites use them. |
| `queue_*_item` (370–429) | Queue purge / paused recovery / orphan recovery | **New** `agent/queue_recovery.py` |
| `assign_work` (914), `pick_issue` (already Python) | Pre-assign issues per worker | Already in `agent/project_loop.pick_issue` (PR #362/#379); reused. |
| `run_employee` (1063), `collect_employee_reports` (1647) | Per-project lead session | Already in `agent/station_orchestrator.py::orchestrate` (#384); reused. |
| `run_manager_review` (1885) | `claude -p` invocation over the review package | **New** `agent/manager_review.py` (or per-issue split to Tier 3 #ISSUE_T3B; this issue ports if T3B has not landed) |
| `execute_verdicts` (2025) | APPROVE/PR/REJECT/SKIP execution | Already in `agent/verdict_execution.py` (PR #380); wire it. |
| `integration-branch.sh::merge_to_dev` (154) | Merge feature → integration branch | **New** `agent/integration_branch.py::merge_to_dev` (Python port; bash file keeps the function for ad-hoc cron use) |
| `write_digest` (2624) | Run digest markdown | **New** `agent/digest.py` |
| EXIT trap + `_send_run_complete_on_exit` (522) | Final webhook | **Deleted**. `RunDriver._emit_run_complete` (`station_orchestrator.py:2372`) already owns it. |

### Driver wiring

Today `agent/project_loop.py::iterate_projects` shells out:

```python
# Today (post-#361)
subprocess.run(
    [str(runmgr), "--internal-iterate"],
    env=env, check=False,
)
```

After #383:

```python
# Target
from agent.preflight import run_preflight
from agent.workspace_setup import ensure_workspace
from agent.queue_recovery import purge_and_recover
from agent.station_orchestrator import orchestrate_project   # extracted per-project entry
from agent.manager_review import run_manager_review
from agent.verdict_execution import execute as execute_verdict
from agent.integration_branch import merge_to_dev
from agent.digest import write_digest

def iterate_projects(run_id, config_path, workspaces_dir) -> int:
    config = load_config(config_path)
    run_preflight(config)
    purge_and_recover(run_id)
    for project in enabled_projects(config):
        ensure_workspace(project, workspaces_dir)
        reports = asyncio.run(orchestrate_project(project, config, run_id, workspaces_dir))
        verdicts = run_manager_review(reports, project, config, run_id)
        for verdict in verdicts:
            result = execute_verdict(verdict, run_id=run_id)
            if result.action == "merge_dev":
                merge_to_dev(project, verdict.branch, verdict.base_branch,
                             verdict.issue_number, verdict.reasoning, workspaces_dir)
    write_digest(run_id, results)
    return 0
```

`orchestrate_project` is an extraction of the per-project portion of today's
`orchestrate()` (`station_orchestrator.py:1667`). The outer "for project in
projects" loop moves to `iterate_projects` so each project's `ClaudeSDKClient`
session is fully scoped.

### `run-manager.sh` itself

Two paths considered:

1. **Full deletion (preferred)**. The file is removed. `agent/launcher.py:34`
   and any other references are deleted; `STATION_RUN_MANAGER` env var is
   dropped from `dashboard/backend/app/settings.py` and the systemd unit.
2. **Shim retention (fallback)**. If a small bash entry point is still
   convenient for ops (e.g., for ad-hoc reruns), reduce the file to ≤200 LOC:
   `set -euo pipefail`, env plumbing, log redirection, then
   `exec python3 -m agent.station_orchestrator --driver "$@"`. No project
   iteration logic, no webhook calls, no queue logic.

The issue acceptance allows either; the design lands on **full deletion**
unless implementation reveals an operator workflow that genuinely needs it.

### Removal of `STATION_LAUNCHER_USE_BASH`

`agent/launcher.py:41` reads the env var; `agent/launcher.py:365–367` selects
the bash entry point when set. Both go away. The launcher's `cmd` is always
the Python driver:

```python
cmd = [
    sys.executable, "-m", "agent.station_orchestrator",
    "--driver",
    "--run-id", driver_run_id,
    "--config", STATION_CONFIG,
    "--workspaces-dir", STATION_WORKSPACES,
]
```

The `if USE_BASH_LAUNCHER and not RUN_MANAGER.is_file()` guard
(`agent/launcher.py:314`) is also removed.

### Telemetry JSON hand-off

`RunDriver._read_bash_telemetry` (`station_orchestrator.py:2323–2340`) exists
solely so the Python driver could read counter values written by the bash side
(`run-<id>-telemetry.json`). With bash gone:

- The counter accumulation already happens inside `handle_stream_event` /
  `_StreamState`. Extract those counters into `RunTelemetry` at end-of-run
  directly. The JSON file is no longer written or read.
- `_read_bash_telemetry` is deleted; `RunDriver.run()`'s `finally` block calls
  a new `_finalize_telemetry(stream_state)` that copies the counts in-process.

### `manager_heartbeat` (#376)

The dashboard-side heartbeat that reaped stuck bash phases (`#376`) loses its
reason to exist — bash phases don't exist anymore. The reaper / heartbeat code
itself stays (it still covers Python-side death), but the bash-specific
`manager_heartbeat` event emission and the phase-state column become dead code.
Cleanup of those is in the issue's "Eliminates downstream issues" list and is
done in-PR.

### `_force_exit_with_cleanup` interaction

If #384 ships first, `_force_exit_with_cleanup` is already deleted before this
issue starts. If #383 has to ship before #384 (it should not, but as a hedge),
keep the function in place — it does no harm with the Python-only path — and
delete it as part of #384.

## Acceptance criteria

Lifted from the issue body, expanded:

- [ ] **`run-manager.sh` deleted or reduced to ≤200 LOC of env plumbing.**
  Concretely: `wc -l agent/scripts/run-manager.sh` returns either zero
  (file removed) or ≤200. CI grep job asserts no remaining `webhook_event`,
  `queue_api`, `setup_workspace`, `run_manager_review`, `execute_verdicts`
  functions in the bash file (or that the file is absent).
- [ ] **All bash phases have Python equivalents with pytest coverage.**
  Each new module (`preflight.py`, `workspace_setup.py`, `queue_recovery.py`,
  `rate_limit.py`, `manager_review.py`, `integration_branch.py`, `digest.py`)
  ships with a `test_<name>.py` under `dashboard/backend/tests/`. Coverage
  for these modules is ≥80% line and exercises the happy + at least one
  failure path per public function.
- [ ] **Launcher path is `python -m agent.station_orchestrator --driver` only.**
  `agent/launcher.py` references no bash command. `grep -n "run-manager" agent/`
  returns zero outside of historical comments / docs.
- [ ] **`STATION_LAUNCHER_USE_BASH` panic-revert flag removed.** Env var and
  its branch are deleted from `agent/launcher.py`; `docs/configuration.md`
  updated accordingly.
- [ ] **Run-20260513T151408Z-equivalent end-to-end smoke test passes.** Same
  2-issue sandbox-repo fixture as #384; reproducibility against the
  reference run is the integration gate. The smoke harness asserts: zero
  bash subprocess invocations from `agent/` modules during the run, all
  webhook events arrive in the expected order, manager review produces a
  verdict file, and `execute_verdict` is invoked at least once.

## Dependencies / blocks

- **Depends on**: [#384](https://github.com/kenhaesler/claude-agent-station/issues/384)
  — without `ClaudeSDKClient`, the deleted bash side no longer has a "safety
  net" Python session that can survive long runs. #384 first, #383 immediately
  after.
- **Blocks**: [#385](https://github.com/kenhaesler/claude-agent-station/issues/385)
  — `RunComplete` tool replaces the prose-heuristic completion check; cleanest
  to drop in after `orchestrate` is single-language.
- **Blocks**: epic-382 Tier 2 issues (manager-heartbeat removal, telemetry
  dump removal) which depend on bash being gone.
- **Eliminates**:
  - Telemetry JSON dump hand-off (`run-<id>-telemetry.json`).
  - `manager_heartbeat` (#376) — bash phase no longer exists.
  - The `os._exit` cleanup hack from PR #381 (the rest goes with #384).

## Risks and rollback

| Risk | Mitigation |
|---|---|
| Subtle bash quoting / env-var behaviour drifts when ported (e.g., `gh issue list` flag quoting). | Each port comes with a golden-file test against recorded bash output for the same inputs. `subprocess.run` calls use list-form (no shell). |
| OAuth token refresh logic in bash (`preflight` calls `refresh-token.py`) has been load-tested implicitly for years. | Python `preflight` calls the *same* `refresh-token.py` script as a subprocess; no logic change, only the caller language. |
| Stale-PR sweep / integration-label creation behaviour drifts between bash and Python merge. | Port `merge_to_dev` first, run it shadow-mode (call Python merge but also call bash and diff outputs) for one release cycle in a feature flag, then flip. Flag default-off; explicit `STATION_INTEGRATION_PY=1` to enable. Remove flag after one release. |
| Hidden bash callers (cron, dashboard endpoints) that still invoke `run-manager.sh`. | `grep -rn "run-manager" .` audit before deletion; archive results in the PR description. |
| `manager_heartbeat` consumers in the dashboard read the column even when no events arrive. | Migrate dashboard handlers in the same PR to treat missing heartbeats as `not_applicable`, not `unknown`. |

**Rollback**: revert the PR. The pre-#383 launcher path (`USE_BASH_LAUNCHER=1`)
is the rollback target; the bash file lives in git history and can be restored
verbatim. Operators flip `STATION_LAUNCHER_USE_BASH=1` until the issue is
re-reverted. *Note*: this only works while `STATION_LAUNCHER_USE_BASH` has not
yet been removed, so the launcher-flag cleanup is the last commit in the PR,
landing **after** the rest of the migration has been observed in production
for one promotion window.

## Test strategy

### Unit tests (per new module)

- `test_preflight.py`: config-missing, deps-missing, OAuth-expired-refresh-ok,
  OAuth-expired-refresh-fail, rate-limit-trip.
- `test_workspace_setup.py`: fresh-clone, refresh-existing, worktree-prune,
  bad-remote-url, mode-branch-checkout.
- `test_queue_recovery.py`: purge old completed, resume paused, recover
  orphaned-from-dead-run, ignore current-run items.
- `test_rate_limit.py`: per-day-cap, per-hour-cap, fresh-state, malformed-sidecar.
- `test_manager_review.py`: happy review, malformed JSON in response,
  `claude -p` non-zero exit, empty review package.
- `test_integration_branch_py.py`: feature-branch-push-ok, push-retry-after-fail,
  PR-create-ok, merge-conflict-detected, dev-branch-bootstrap.
- `test_digest_py.py`: empty-run, multi-verdict run, error in verdict.

### Integration

- `dashboard/backend/tests/test_iterate_projects_python.py`: end-to-end
  `iterate_projects` against a sandbox config with all subprocess boundaries
  (git, gh, claude-p) mocked. Assert the full phase sequence executes and
  webhooks fire in the same order as the recorded bash sequence.

### Smoke (reused)

- The run-20260513T151408Z reference fixture from #384, re-run here against
  the all-Python path. Same assertions plus:
  - `grep -c "bash" /var/log/claude-agent/run-<id>.log` returns zero
    `run-manager.sh` invocations.
  - The telemetry JSON file is **not** written.

### Migration / parity (shadow mode for merge_to_dev)

- `STATION_INTEGRATION_PY_SHADOW=1` runs both Python and bash `merge_to_dev`
  serially, diffs their git output. Land in the same PR. Surface diffs as
  test failures in CI for one release before removing the bash path entirely.

## Notes / open questions

- **Should `agent/scripts/run-manager.sh` be deleted or reduced to a shim?**
  Implementation decision — defer until the ports are done. If ops staff still
  reach for the script during incident response, retain the ≤200-LOC shim.
- **Tier 3 #ISSUE_T3B owns the manager-review port.** If #ISSUE_T3B lands
  first, the `manager_review.py` row in the port table is "already done";
  this issue's scope just wires it. If not, this issue does the port itself.
  Either way the function signature is `run_manager_review(review_package_path,
  run_id, config) -> list[Verdict]`.
- **Worktree-prune logic** in `setup_workspace` interacts with the worktrees
  `orchestrate_project` creates. Confirm at implementation time whether the
  port can move that responsibility into `orchestrate_project` itself (simpler)
  versus a pre-clean step (parity with today). Either is acceptable.

> **Note**: As of this writing, `agent/scripts/run-manager.sh` is 3309 LOC and
> the issue body cites 3239 LOC. The discrepancy is just drift; the deletion
> target is the whole file.
