# Spec — Issue #376: Manager review heartbeat

**Status**: spec
**Design**: [`docs/design/issue-376.md`](../design/issue-376.md)
**Issue**: [#376](https://github.com/kenhaesler/claude-agent-station/issues/376)
**Targets**: `dev`

## Acceptance (from issue)

- [ ] A successful 3-teammate full-mode run with a 5+ minute manager review phase completes without being reaped.
- [ ] Launcher's `_zombie_reaper` does NOT fire during manager review.
- [ ] Dashboard's `stale_run_reaper` does NOT mark the run interrupted during manager review.
- [ ] Test: simulate a long bash phase with no orchestrator webhooks and assert the run completes.

## Files changed

| Path | Action | Approx LOC |
|---|---|---|
| `agent/scripts/run-manager.sh` | edit (`run_manager_review` function, ~line 1871) | +25 |
| `agent/scripts/tests/test_manager_heartbeat.bats` | new (or shell-based smoke) | +40 |

No other files. No Python changes. No dashboard changes.

## Implementation steps

### 1. Add the heartbeat helper

Inside `run_manager_review()` in `agent/scripts/run-manager.sh`, before the `cd "$WORKSPACES_DIR"` and `claude -p` invocation:

```bash
# Fork a background loop that pings the launcher every 30 s during the
# manager review. The bash does NOT parse the manager's stdout (which is
# streamed to a file), so without this the launcher's _zombie_reaper kills
# the run after 120 s of webhook silence. See issue #376.
(
    # Detach from job control so the trap-driven kill below is reliable.
    while sleep 30; do
        webhook_event "manager_heartbeat" phase "manager_review" >/dev/null 2>&1 || true
    done
) &
local _heartbeat_pid=$!
# Kill the heartbeat on any exit from this function — normal return, error,
# parent signal. Use a function-local trap that restores afterwards.
trap 'kill '"$_heartbeat_pid"' 2>/dev/null || true' RETURN
```

The `RETURN` trap is bash 4+ and fires when the function returns by any path (return, error, last-statement). It does not collide with the global `EXIT` trap that handles run-level finalization.

If the bash interpreter version on the target host does not support function-scoped `RETURN` traps, fall back to an explicit `kill "$_heartbeat_pid" 2>/dev/null || true` at every `return` path inside `run_manager_review` plus a `trap … EXIT` register-and-restore dance. Spec assumes bash ≥ 4.0 (Rocky Linux 9 default is 5.1).

### 2. Confirm `manager_heartbeat` is treated as a benign event

The webhook handler at `dashboard/backend/app/routers/webhook.py` already bumps `Run.last_event_at` for every event (per #348). No frontend change needed; the timeline view filters known event types and ignores unknown ones.

Verify by reading the webhook router: confirm there is no `match`/`if` that throws on unknown `event_type`. If there is, add `manager_heartbeat` to the known set with no side effects beyond the heartbeat bump.

### 3. Test

`agent/scripts/tests/test_manager_heartbeat.sh` (new):

```bash
#!/usr/bin/env bash
# Smoke test: run_manager_review's heartbeat fires at least twice during a
# 90s simulated manager run.
set -euo pipefail

WEBHOOK_COUNTER=$(mktemp)
echo 0 > "$WEBHOOK_COUNTER"

# Stub webhook_event to a counter.
webhook_event() {
    if [ "$1" = "manager_heartbeat" ]; then
        local n
        n=$(cat "$WEBHOOK_COUNTER")
        echo $((n+1)) > "$WEBHOOK_COUNTER"
    fi
}
export -f webhook_event

# Stub claude binary to sleep instead.
fake_claude() { sleep 90; }
export -f fake_claude

# Source run-manager.sh in a mode that lets us call the function directly,
# with the heartbeat patched in. (Implementation may need a tiny --test-mode
# flag in run-manager.sh; if so, add it.)

# Source library, invoke run_manager_review with stubs, then:
count=$(cat "$WEBHOOK_COUNTER")
[ "$count" -ge 2 ] || { echo "FAIL: heartbeat fired $count times, expected ≥ 2"; exit 1; }
echo "PASS: heartbeat fired $count times"
```

If extracting `run_manager_review` for direct invocation proves too invasive, accept manual verification only and document the manual test plan in the PR body. The dashboard observation (no reaper fires during a real 5-min manager review) is the load-bearing test.

### 4. Documentation

Add a one-line note in `docs/architecture.md` under the run-manager phase description: "Manager review phase emits a `manager_heartbeat` webhook every 30 s while `claude -p` runs (#376) so the zombie reapers don't kill it."

## Risks

- **Trap clobbering.** If the global `EXIT` trap and the new `RETURN` trap interact badly, run cleanup could be skipped. Mitigation: `RETURN` is function-scoped; it cannot override `EXIT`. Read the existing EXIT trap registration once to confirm.
- **Zombie heartbeat after SIGKILL.** If the parent bash gets SIGKILL'd (not SIGTERM), the trap won't fire and the subshell leaks. Mitigation: the subshell's parent-PID check on its `webhook_event` POST would catch this; alternatively, set the subshell's parent-death-signal via `prctl` (not portably available in bash). Accepted risk: SIGKILL leaks one subshell that exits when its next `webhook_event` HTTP call sees the launcher-side `/webhook-tick` return `stale=true` (or simply fails). Low impact; cleared on next run.
- **30 s × N runs flood the webhook log.** Each event hits `runs.last_event_at` and the events table. 30 s cadence × 5 min = 10 events. Negligible.

## Rollback

Single commit, single file. `git revert <sha>` and re-deploy.

## Out of scope

- Per-turn manager progress webhooks (issue option 2). Filed as a follow-up.
- Heartbeats for other bash phases. Audit during PR review whether any other `claude -p` call site has the same gap.
