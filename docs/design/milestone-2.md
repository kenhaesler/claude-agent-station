# Design — Milestone 2: Bash → Python migration (issues #361, #362, #363)

**Status**: design
**Date**: 2026-05-12
**Issues**: [#361](https://github.com/kenhaesler/claude-agent-station/issues/361), [#362](https://github.com/kenhaesler/claude-agent-station/issues/362), [#363](https://github.com/kenhaesler/claude-agent-station/issues/363)
**Targets**: `dev`
**Extends**: [`docs/superpowers/specs/2026-05-11-run-lifecycle-overhaul-design.md`](../superpowers/specs/2026-05-11-run-lifecycle-overhaul-design.md) — Item 5

## Context

Milestone 1 (#349) landed three sub-PRs that built the bash→Python migration scaffolding:

- **5a** — `agent/webhook_emitter.py`: one Python entrypoint for every webhook the bash emits. Adds retries; bash continues to call it as a subprocess.
- **5b** — `agent/coordinator_lifecycle.py`: lifecycle for `coordinator_tasks` rows, with an atexit fail-safe.
- **5c** — `agent/station_orchestrator.py::RunDriver` (lines 2143–2194): a thin class that emits `run_start` and `run_complete` in a Python `try/finally`, delegating per-project iteration to `agent/project_loop.py::iterate_projects` — which today shells back to `run-manager.sh --internal-iterate`.

`RunDriver` is currently gated behind `--driver` and is **not** invoked by anything in production. Three concrete consequences:

1. Production runs still flow through the bash EXIT-trap that M1 set out to eliminate.
2. `RunDriver` only emits `run_start` and `run_complete` with `{status}`; bash emits a wider payload (project_count, max_concurrent, concurrent_group_id, tokens_input/output, duration_ms, etc.) that the dashboard reads.
3. SIGINT on a `--driver` run today produces `status="error"` (the `except Exception` branch caught the KeyboardInterrupt) — bash mapped exit code 130 to `interrupted`.

Milestone 2 closes those gaps and **deletes** the bash bodies that M1 left in place behind the shim.

## Goals

- `RunDriver` is the production launcher caller. `agent/launcher.py::_spawn_run_manager` no longer execs `run-manager.sh` for the orchestration path.
- Webhook payload parity: every field the bash used to emit on `run_start`/`run_complete` is emitted by Python, verified by a pytest golden-file comparison.
- SIGINT/SIGTERM produces `status="interrupted"` (exit code 130 mapped, KeyboardInterrupt handled distinctly from generic `Exception`).
- Per-project iteration runs in Python: issue picking via `gh` subprocess + JSON parse, dispatch as an in-process call into the Agent Teams orchestrator (no more `subprocess` for the dispatch path).
- Verdict execution (approve / open PR / reject / skip) runs in Python: `gh pr create`, `git push`, `gh issue comment`, label edits all happen via the new `agent/verdict_execution.py` module.
- `run-manager.sh` either shrinks to ≤ 200 LOC of env/log/lock plumbing **or** is deleted entirely. The 3239-LOC bash dragon is gone.

## Non-goals

- Rewriting `integration-branch.sh`, `promote.sh`, `resolve-conflicts.sh`, `circuit-breaker.sh`, `sprint-cycle.sh`. Per the M1 design, these stay as bash.
- Changing webhook payload shapes, `run_id` semantics, or any dashboard contract. Field-for-field compat.
- Async overhaul. Synchronous `subprocess.run` everywhere; `concurrent.futures` only where bash used `&`.
- Issue #376 (bash manager-review heartbeat) — ships separately as a same-day fix; M2 deletes the bash that #376 patches.

## High-level shape

```
launcher.py::_spawn_run_manager
       │
       ▼
python3 -m agent.station_orchestrator --driver --run-id … --config … --workspaces-dir …
       │
       ▼
RunDriver.run()              # 5c, enriched in #361
   │   emits run_start with full payload
   │   try:
   │      iterate_projects(run_id, config_path, workspaces_dir)
   │           │
   │           ▼  (today: subprocess→bash. After #362: native Python.)
   │      project_loop.iterate_projects()
   │         for each project:
   │            issue = _pick_issue(project)         # #362
   │            run agent teams (orchestrator API)   # #362, in-process
   │            for each verdict:
   │               verdict_execution.execute(...)    # #363
   │   finally:
   │      emit run_complete (status incl. 'interrupted')
```

The three issues are decoupled by interface boundaries already in place: `RunDriver` doesn't care how `iterate_projects` produces its result; `iterate_projects` doesn't care how `execute_verdict` makes `gh pr create` happen. Once #361 wires the path, #362 and #363 can land in either order.

## Per-issue scope

### #361 — Wire RunDriver as production launcher path

**Items 4+5+6 of #349's deferred list.**

1. **Launcher integration.** `agent/launcher.py::_spawn_run_manager` builds and execs `python3 -m agent.station_orchestrator --driver --run-id <id> --config <path> --workspaces-dir <path>`. The legacy `run-manager.sh` path is removed (or kept behind an env flag for one release as a panic-revert escape hatch — design decision in spec).
2. **Payload parity.** `RunDriver.run()` reads `project_count`, `max_concurrent`, `concurrent_group_id` from config at startup and includes them in the `run_start` payload. The `run_complete` payload accumulates `tokens_input`, `tokens_output`, `tokens_total`, `turns`, `duration_ms`, and `exit_code`. Source of truth for token totals is the orchestrator's existing accumulator (already used by the bash via env-roundtrip; M2 reads it directly).
3. **Signal handling.** `RunDriver.run()` catches `KeyboardInterrupt` separately from `Exception`. On `KeyboardInterrupt`: status = `"interrupted"`, exit code 130, re-raise after `finally`. On exit code 130 from a child subprocess (e.g. the orchestrator was killed by signal): also map to `interrupted`. The `finally` block always fires before re-raise.

### #362 — Port project loop iteration to Python

**Items 1+2 of #349's deferred list.**

1. **Issue picking.** Replace the bash block around `run-manager.sh` lines 1900–2100 (gh issue list + label filtering) with a Python function `_pick_issue(project, gh_token) -> IssueDesc | None` that runs `gh issue list --json …` via `subprocess.run`, parses the JSON, and applies the same filter logic the bash had (skip `backlog`-labeled, skip claimed-by-running-tasks, prefer oldest).
2. **In-process dispatch.** Bash invocation at `run-manager.sh:2757` (`python3 -m agent.station_orchestrator …` subprocess) becomes a direct Python call into the orchestrator's public API. The orchestrator's `orchestrate()` coroutine is the natural target; we call it via `asyncio.run` from the synchronous `iterate_projects`.
3. **Delete bash.** The picking and dispatch blocks come out of `run-manager.sh`.

### #363 — Port verdict execution path to Python

**Item 3 of #349's deferred list.**

1. **New module** `agent/verdict_execution.py` exposing:
   - `execute_approve(verdict, branch, base_branch) -> ExecutionResult`
   - `execute_pr(verdict, branch, base_branch, draft) -> ExecutionResult`
   - `execute_reject(verdict, reason) -> ExecutionResult`
   - `execute_skip(verdict, reason) -> ExecutionResult`
   Each wraps the corresponding `git`/`gh` invocations the bash currently makes (lines ~2100–2500 of `run-manager.sh`).
2. **Caller.** `project_loop.py` (now native after #362) calls the appropriate executor per verdict after the manager review writes its verdicts JSON.
3. **Conflict resolution.** The existing `agent/conflict_resolver/` is a separate subprocess; #363 keeps that interface. Verdict execution may invoke it the same way the bash did.
4. **Delete bash.** Verdict execution blocks come out of `run-manager.sh`.

## Cross-cutting design decisions

- **Atomicity of payload parity.** Item #361 lands the parity AND the wiring. We do not land "wired but blank payloads" — the dashboard would render incomplete rows for a release. Either both ship or neither (one PR).
- **Subprocess vs in-process for `gh`.** `gh` stays as a subprocess; there is no Python binding worth the dependency. We standardise on a thin `agent/gh_client.py` helper (introduce here if it doesn't exist) for command construction + JSON parsing + error wrapping. Avoids `shlex` traps and centralises error handling.
- **Signal propagation.** Python child processes (`gh`, `git`, `claude`) launched via `subprocess.run` inherit the process group by default. On SIGINT, they receive the signal and exit; `subprocess.run` raises CalledProcessError. The driver-level catch translates this to `status="interrupted"`.
- **Concurrency.** Where bash used `&` for parallel project work, Python uses `concurrent.futures.ThreadPoolExecutor` (synchronous I/O, just parallel subprocess calls). No asyncio in the iteration body — keeps the call site shape similar to the bash for review.
- **Logging.** Python writes to the same `/var/log/claude-agent/run-<RUN_ID>-launcher.out` the bash writes to. We exec rather than fork-and-pipe, so no interleaving.

## Risks and mitigations

- **Bash quoting parity.** `gh` invocations from bash use ad-hoc quoting; Python subprocess calls take an argv list. The risk is missing a `--field-with-value` corner case. Mitigation: golden-file tests that record the exact argv `verdict_execution` produces, reviewed against the bash form.
- **Token accumulator hand-off.** Bash kept token totals in env vars (`_TOTAL_TOKENS_IN`, `_TOTAL_TOKENS_OUT`). Python should read them from the orchestrator's in-memory state, not env. Mitigation: explicit data class for run telemetry, populated by the orchestrator, consumed by `RunDriver`.
- **Two-stage migration regression window.** Once #361 lands, #362 still calls bash via `--internal-iterate`. If `--internal-iterate` has untested edges, we'll find them in production. Mitigation: pre-flight on a staging trigger run; revert env flag if catastrophic.
- **Signal handling on Linux process groups.** If `subprocess.run` calls don't share the group as expected, SIGINT may not propagate to `claude`. Mitigation: explicit `start_new_session=False` (default) plus integration test that fires SIGINT and asserts `claude` exits.

## Test strategy

- **Per-issue pytest** with mocked `gh`/`git`/subprocess calls. Golden-file payload comparison for `run_start`/`run_complete`.
- **End-to-end smoke** in the dev container: trigger a real run after each PR lands, observe dashboard fields are all populated.
- **SIGINT integration test** (#361): start a run, send SIGINT, assert `runs.status="interrupted"` in the DB.

## Phasing

- **#361 first.** Foundation for the others. Without payload parity + signal handling, neither #362 nor #363 is safe to merge.
- **#362 and #363 in either order** after #361. They touch different bash blocks and different Python modules.
- Each PR lands on `dev`. User promotes to `main` per project conventions.

## Open questions (to be resolved in spec/implementation)

1. Does the launcher keep a panic-revert env flag (`STATION_LAUNCHER_USE_BASH=1`) for one release? Recommend: yes, gated to a single release cycle, then removed.
2. Is the bash shim worth keeping at ~200 LOC (env/log/lock setup) or do we move that into Python too? Recommend: move into Python — the env setup is trivial, the lock is `fcntl.flock`, the log redirect is `os.dup2`.
3. How aggressive should the bash deletion be? Recommend: delete the per-PR scope only — #361 deletes the launcher invocation path; #362 deletes picking+dispatch blocks; #363 deletes verdict blocks. After all three, the remaining `run-manager.sh` should be the standalone scripts only (or zero, if we move env setup into Python).
