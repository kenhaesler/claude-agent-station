> **TL;DR** — When weekly Claude usage gets too high, runs are paused before they start so the agent doesn't run out of budget mid-task.

Claude Agent Station is bounded by the Claude plan tier you've configured. The station tracks weekly token consumption and refuses to start a new run when usage crosses the threshold — better to pause cleanly than to fail mid-task with no budget to recover.

There are two protections:

1. **Plan-tier throttle.** Weekly usage (overall and per-model) is checked before each run. If the budget is too low, `run-manager.sh` short-circuits before launching any agent.
2. **Per-model fallback.** Every Claude invocation passes a `--fallback-model` to the SDK so primary-model errors don't kill the run. Opus 4.7 falls back to Sonnet 4.6, Sonnet 4.6 falls back to Haiku 4.5.

The Command Center surfaces current usage and the active throttle state.

<!-- under-the-hood -->

- Usage history: `plan_usage_history` table.
- Detection: `agent/scripts/detect_plan_usage.py`.
- Throttle decision API: served from the dashboard backend; consumed by Command Center.
- Throttling is independent of the fallback chain — both can be active.
