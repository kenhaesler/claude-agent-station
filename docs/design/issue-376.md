# Design — Issue #376: Heartbeat coverage during bash manager review

**Status**: design
**Date**: 2026-05-12
**Issue**: [#376](https://github.com/kenhaesler/claude-agent-station/issues/376)
**Targets**: `dev`

## Context

After implementation phase ends, `agent/scripts/run-manager.sh::run_manager_review()` (around line 1871) spawns `claude -p --output-format stream-json` to evaluate employee work. Stdout is redirected to `run-<RUN_ID>-manager.stream.jsonl`; the bash itself does not parse turns and does not emit webhooks while the manager is thinking.

The launcher's `_zombie_reaper` (`agent/launcher.py:217`) and the dashboard's `stale_run_reaper` both interpret a >120 s webhook silence as a hung run and kill it. Reproduced in `run-20260512T133721Z`: the manager was actively progressing (logs show per-turn manager output), but no `webhook-tick` calls reached the launcher, so SIGTERM/SIGKILL fired at 133 s of silence — even though the manager review was healthy and partway through real work.

The implementation phase avoids this because `station_orchestrator.py` streams from the Agent SDK and emits a webhook per narration / progress event. The bash-driven manager review has no equivalent.

## Goals

- A long-running manager review phase (5–10 minutes is plausible on a multi-project run) does not get killed by either reaper.
- Heartbeats only fire while the manager is actually running — no leaked background loops after `run_manager_review` returns.
- The fix is local to bash; no Python or dashboard changes required.
- The fix is small enough to land independently of the milestone-2 bash→Python migration. Once M2 lands, this hack disappears with the rest of `run_manager_review`.

## Non-goals

- Operator-visible manager progress in the dashboard. That is the issue's "option 2" — stream-parse the manager output and emit per-turn webhooks. Bigger change, deferred to a follow-up issue.
- Bumping the reaper timeout. That masks the problem and weakens detection for genuinely-hung runs (the issue's "option 3", explicitly rejected).
- Touching the implementation-phase webhook flow.

## Approach

**Option 1 from the issue** — background heartbeat shell loop, killed on `run_manager_review` exit.

1. Before invoking `claude -p`, fork a subshell that emits `webhook_event manager_heartbeat` every 30 s. Capture the subshell's PID.
2. Run the `claude -p | while read …` pipeline as today.
3. After the pipeline exits, `kill` the heartbeat subshell. Use a `trap` so the heartbeat is killed even if `run_manager_review` returns early (error path) or the parent receives a signal.

### Why 30 s

- Reaper timeout is 120 s. 30 s gives 3 chances before timeout; one missed iteration is recoverable, two consecutive misses still doesn't trip the reaper.
- Cheap — each iteration is one HTTP POST to `/webhook` + one to `/webhook-tick`. Both already exist; no new endpoints.

### Why a new event type (`manager_heartbeat`)

- Dashboards already filter event types. Reusing an existing event (e.g., `manager_review`) would either double-count or mislead the timeline view.
- A new no-op event with `event_type="manager_heartbeat"` lands in the events log, is ignored by the timeline UI, and bumps `Run.last_event_at` plus the launcher's `_last_webhook_at` — exactly what the reapers care about.

### Subprocess lifecycle

The heartbeat subshell must die when:
- `run_manager_review` returns normally (claude exited)
- `run_manager_review` errors out before the kill
- The parent bash receives SIGTERM/SIGKILL (e.g., from the existing reaper for genuinely-hung claude)

`trap "kill $HEARTBEAT_PID 2>/dev/null || true" EXIT INT TERM` inside `run_manager_review` is the cleanest local trap. We must save and restore any outer EXIT trap if one exists — `run-manager.sh` already has an EXIT trap for finalization; we use a function-local trap pattern that doesn't clobber the global one.

## Alternatives considered

- **Issue option 2 (stream-parse manager output and emit per-turn webhooks).** Better operator UX (manager progress visible in the dashboard), but requires non-trivial bash JSON wrangling. The bash already does shallow parsing for log lines (line 1940–1965); extending it to emit a `manager_progress` webhook per turn is doable but invasive. Deferred: file a follow-up issue once #376 lands.
- **Issue option 3 (bump `ZOMBIE_TIMEOUT_SECONDS` to 600 for the manager phase).** Two problems: (a) the launcher doesn't currently know which phase the bash is in, so we'd need new IPC; (b) masks the real observability gap — a manager that genuinely hangs after 30 s of silence should still be reaped, just on a per-event basis.
- **Move the manager review into Python now.** Correct long-term direction, but it belongs in the milestone-2 work, not a same-day hotfix. Doing it here would inflate scope and likely regress.

## Testing

- **Unit (bash)**: a small bats/shell test that stubs `webhook_event` to a counter file, runs `run_manager_review` with `claude` replaced by `sleep 90`, and asserts the counter is ≥ 2 (two 30 s intervals).
- **Integration**: a triggered run with a deliberately slow manager (e.g. via `--max-turns 50` on a large review package) completes without being reaped. Manual; checklist in PR.

## Rollback

Single commit on `run-manager.sh`. Revertable in one click; no schema, no API, no shared state.

## Out of scope (follow-ups)

1. Per-turn manager progress webhooks (issue option 2).
2. Same fix for any other long bash-driven phases (assigner_start? planner_complete?). A quick audit of all `claude -p` invocations in `run-manager.sh` will confirm if other phases share the gap.
