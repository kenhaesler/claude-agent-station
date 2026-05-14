# Drop `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` Workaround — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: #392 (Tier 3 / D of epic #382)
**Depends on**: Tier 1B — `ClaudeSDKClient` migration
**Blocks**: nothing (cleanup)

## Context

`agent/launcher.py:339` sets a defensive environment variable before
spawning the run-manager subprocess:

```python
env.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")
```

This was introduced by PR #371 to mitigate an SDK failure mode: the
bundled Claude CLI begins a stdin-close countdown after emitting its first
`ResultMessage`, and once stdin closes every queued `PreToolUse` /
`PostToolUse` hook callback raises `Error: Stream closed`
(`cli.js:7552 sendRequest`). In production this manifested as
`[hook-cb-fail]` warnings and silent audit-log dropouts roughly one to two
minutes into long Agent Teams sessions. Bumping the timeout from the
60-second default to 30 minutes pushed the symptom past the typical run
duration without changing the underlying behaviour.

PR #381's root-cause investigation reclassified this env var as
**effectively a no-op for the orchestrator path**: it only sets the
*maximum* wait before stdin closes, not the policy. The real fix landed
in Tier 1B's `ClaudeSDKClient` migration, whose lifecycle is owned by the
orchestrator (the client stays open for the duration of `async with
ClaudeSDKClient(...) as client:` and is torn down deterministically).
`ClaudeSDKClient` does not consult `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` for
its lifecycle.

This spec removes the env var from `launcher.py` and the regression test
that pins it in place, leaving zero "magic env var" residue from the
hook-callback era.

> **Note**: the issue body asserts that `agent/vision_analyst.py` or
> `agent/plan_review_gate.py` may still depend on `query()` and therefore
> on this env var. Verification (2026-05-14): neither module imports
> `claude_agent_sdk`. `vision_analyst.py:179` shells out via
> `subprocess.run(["claude", "--print", ...])`, and `plan_review_gate.py`
> performs no model calls of its own. The remaining `query()` call site is
> `agent/conflict_resolver/sdk_runner.py:95`; whether that path needs the
> env var is the audit question for this issue.

## Goals

- Remove `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` from `agent/launcher.py` once
  Tier 1B has landed and the orchestrator no longer relies on the bundled
  CLI's hook-callback lifetime.
- Delete the test that asserts the env var is set, so the CI suite stops
  reinforcing the workaround.
- Audit every `query()` / `claude --print` call site and either justify
  its continued reliance on the env var (with a focused comment) or
  confirm independence.

## Non-goals

- Migrating `conflict_resolver/sdk_runner.py` to `ClaudeSDKClient` — that
  module's lifecycle is short-lived and its hook usage is bounded; if a
  surviving dependency on the env var is found there it can be either
  retained locally or migrated under a follow-up issue.
- Touching `agent/vision_analyst.py`'s `subprocess` wrapper. The bundled
  CLI's stdin-close behaviour does not affect a one-shot `claude --print`
  invocation that exits immediately after the result message.

## Approach

### Step 1 — Audit call sites

Enumerate every place the agent reaches for the bundled CLI or the SDK
query primitive:

| Caller | Mechanism | Notes |
|---|---|---|
| `agent/station_orchestrator.py:2047` | `query(prompt=..., options=...)` | Migrated to `ClaudeSDKClient` by Tier 1B; lifecycle owned by client context manager. |
| `agent/conflict_resolver/sdk_runner.py:95` | `query(prompt=..., options=...)` | Short-lived, one issue at a time. Hook usage via `make_pre_tool_hook` / `make_post_tool_hook`. Decide: keep or migrate. |
| `agent/vision_analyst.py:179` | `subprocess.run(["claude", "--print", ...])` | One-shot CLI invocation; not affected. |
| `agent/scripts/run-manager.sh:1499` (lead) | `claude -p` (bash) | Deleted by Tier 1A. |
| `agent/scripts/run-manager.sh:1924` (manager) | `claude -p` (bash) | Deleted by Issue #390. |

### Step 2 — Decide on `conflict_resolver/sdk_runner.py`

Either:

1. **Migrate**: convert `sdk_runner.py` to `ClaudeSDKClient` (pattern
   borrowed from Tier 1B's orchestrator migration). Removes the last
   surviving `query()` caller in the agent codebase. Cleanest end-state.
2. **Document**: leave the `query()` call in place and set
   `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` locally in that module's env-prep
   helper (alongside a focused comment pointing to PR #381). The launcher
   stops being the policy owner.

Recommendation: option 2 for this issue's scope. Option 1 is a separate
refactor that does not block #392's stated outcome.

### Step 3 — Remove from `agent/launcher.py`

Delete lines 327–339 (the multi-line explanatory comment and the
`env.setdefault` call). The hint-run-id propagation immediately below is
unrelated and stays.

### Step 4 — Remove the regression test

`dashboard/backend/tests/test_orchestrator_wiring.py::test_launcher_sets_stream_close_timeout_in_run_env`
(lines ~1643–1670) asserts via source-string inspection that
`agent.launcher.trigger()` sets the env var. Delete the test function and
the explanatory docstring above it.

### Step 5 — Add a forward-pointing comment (optional)

If option 2 was taken for step 2, add a one-line comment near
`sdk_runner.py`'s `query()` call:

```python
# This path still relies on the bundled CLI's hook lifecycle; the
# stream-close timeout is set in this module's env-prep helper.
```

No comment is needed in `launcher.py` after deletion — the absence is
the documentation.

## Acceptance criteria

Quoted from the issue body (#392), expanded:

- [ ] **"All `query()` call sites audited and either migrated or
      documented"** — produce a one-screen audit table (similar to the
      one above) in the PR description. Every row must end in either
      "migrated to `ClaudeSDKClient`", "documented in module-local env
      prep", or "n/a — short-lived subprocess".
- [ ] **"`CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` line removed from
      `agent/launcher.py`"** — lines 327–339 deleted. `grep -rn
      CLAUDE_CODE_STREAM_CLOSE_TIMEOUT agent/launcher.py` returns nothing.
- [ ] **"Test referencing the env var removed"** — the test function in
      `test_orchestrator_wiring.py` and any helpers it imports are
      removed. `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT
      dashboard/backend/tests/` returns nothing.
- [ ] **"No regression: production run completes within
      `ClaudeSDKClient`'s native lifecycle"** — verified by running one
      end-to-end issue through the orchestrator post-Tier-1B and
      confirming no `[hook-cb-fail]` warnings or audit-log dropouts in
      `/var/log/claude-agent/run-<id>-launcher.out`.

## Dependencies / blocks

- **Hard dependency**: Tier 1B. Removing the env var before
  `ClaudeSDKClient` lands re-exposes the original PR #371 failure mode.
- **Soft dependency**: Issue #389 (audit hook removal) — removing
  `[hook-cb-fail]` makes the regression signal cleaner, but #392 can
  ship without #389.
- Blocks: nothing.

## Risks and rollback

- **Risk**: a hook-callback dependency in `sdk_runner.py` (or a future
  module) re-introduces the old failure mode. Detection: the
  `[hook-cb-fail]` log prefix remains in place until Issue #389 deletes
  the audit hook entirely.
- **Risk**: an out-of-tree caller (an operator's local
  `STATION_VISION_ANALYST_MODEL`-style script) relied on the launcher
  re-exporting the env var. Mitigation: the launcher only sets the var
  *for the run-manager subprocess*; no third-party caller can rely on
  inherited environment unless they themselves spawn the launcher.
- **Rollback**: revert the launcher delta. The test deletion is
  independently revertable. No DB migrations, no webhook payload changes.

## Test strategy

- **Static**: `grep` assertions in the PR description ensure no orphan
  references survive in `agent/`, `dashboard/backend/`, or `docs/`.
- **Unit**: no new tests required; the work is a deletion.
- **Integration**: existing `tests/test_run_lifecycle.py` continues to
  exercise the launcher path. Add a single negative assertion (optional)
  that `subprocess.Popen`'s `env` argument does not contain the key, to
  pin the deletion in place.
- **Manual**: trigger one full Agent Teams run on the dev box post-merge,
  confirm `tail -F /var/log/claude-agent/run-*-launcher.out` shows no
  `[hook-cb-fail]` warnings and the audit_log has continuous rows
  through the entire run.
