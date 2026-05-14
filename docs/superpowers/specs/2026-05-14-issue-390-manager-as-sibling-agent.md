# Manager as Sibling Agent — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: #390 (Tier 3 / B of epic #382)
**Depends on**: Tier 1A — bash deletion / Python-driven run loop; Tier 1B — `ClaudeSDKClient`

## Context

Today the manager review is a separate process invoked by the run-manager
shell. After the lead and three specialist teammates complete their work
in a single SDK session, `agent/scripts/run-manager.sh:1885`
(`run_manager_review`) spawns a **second** Claude process — a bare
`claude -p` invocation — to read the review package and produce verdicts:

```bash
local -a cmd=(claude -p --verbose --output-format stream-json \
              --no-session-persistence --dangerously-skip-permissions)
cmd+=(--model "$model")
cmd+=(--fallback-model "$manager_fallback")
cmd+=(--max-turns "$max_turns")
cmd+=(--system-prompt-file "$(resolve_prompt manager)")
# ...
"${cmd[@]}" -- "$manager_prompt" 2>>"$stderr_file" | \
while IFS= read -r line; do
    echo "$line" >> "$stream_file"
    # … parse stream-json into per-event log lines …
done
```

This separation is the structural cause of four operational scars:

1. **PR #376 (manager heartbeat)**. The bash phase emits no SDK
   webhooks, so the dashboard's stale-run reaper would kill an
   otherwise-healthy review at the 120-second mark. A subshell
   (`run-manager.sh:1956-1962`) fires a synthetic `manager_heartbeat`
   webhook every 30 s to keep the run alive.
2. **Audit-log gap during manager review**. The manager process is a
   different OS process with no `make_pre_tool_hook` /
   `make_post_tool_hook` registration, so its tool calls are absent
   from `audit_log`. Operators see a tool-activity void corresponding
   to the manager review window. (Issue #389's stream-derived audit
   does *not* fix this either, because the orchestrator never sees
   the manager's stream; only the bash does.)
3. **Separate `manager.stream.jsonl` file**. The bash redirects the
   manager's `stream-json` output to
   `LOG_DIR/run-${RUN_ID}-manager.stream.jsonl`
   (`run-manager.sh:1968`) and parses it line-by-line. Operators
   debugging a run must consult two stream files instead of one.
4. **30-turn hard ceiling**. `max_manager_turns` defaults to 30 via
   `--max-turns`. A review that genuinely needs more turns (large
   diffs, many projects) hits the ceiling and produces no verdict
   file, wasting the entire run's work.

Agent Teams natively supports the pattern we want: the lead spawns
sibling agents via the Agent tool, each with its own role / prompt /
model, all on the same SDK session and the same orchestrator event
stream. We use this today for `backend` / `frontend` / `qa` teammates.
Adding `manager` as a fourth sibling — invoked by the lead *after* the
three specialists have produced their work — folds the manager review
into the existing single-session, single-stream lifecycle.

## Goals

- A run executes inside a single SDK session, from lead launch through
  manager verdict, with no second process.
- The manager review's tool calls are visible in the same stream that
  carries lead and teammate activity, written to the same `audit_log`,
  counted toward the same `tokens_total`.
- PR #376's heartbeat machinery is removed; the heartbeat becomes the
  ordinary `progress_update` cadence the orchestrator already emits.
- The hard 30-turn manager ceiling is replaced by the lead's overall
  turn budget, leaving the manager free to use as many turns as the
  review genuinely needs (within the run's global cap).

## Non-goals

- Reshaping the manager's review criteria, verdict schema, or per-mode
  branching (`ANALYZE` / `PLAN` / `PLAN_REVIEW` / `FULL`). The prompt
  contract is unchanged.
- Removing the verdict file. The orchestrator still reads the verdict
  JSON from `run-${RUN_ID}-verdicts.json` and feeds it to the
  post-review state machine.
- Reviving an old per-project manager loop. The current single-call
  pattern (one manager invocation reviewing all projects in a run) is
  preserved.

## Approach

### Add the manager agent definition

> **Note**: the issue body asserts `agent/agents/manager.md` "already
> exists". Verification (2026-05-14): the only file in `agent/agents/` is
> `issue-worker.md`. The manager prompt lives at
> `agent/prompts/manager.md`, intended for the bash `--system-prompt-file`
> invocation. This spec creates `agent/agents/manager.md` as a new file.

The new `agent/agents/manager.md` has the standard Agent Teams
frontmatter:

```yaml
---
name: manager
description: Reviews work produced by backend / frontend / qa teammates and writes verdict JSON.
tools: Read, Edit, Write, Bash, Glob, Grep
model: claude-sonnet-4-6
permissionMode: bypassPermissions
maxTurns: 60
---
```

The body is the contents of `agent/prompts/manager.md` lightly adapted:

- Replace "You are running via `claude -p`" (`prompts/manager.md:15`)
  with "You are running as an Agent Teams sibling spawned by the lead".
- Replace "The verdict file path is provided in your user prompt" with
  the same instruction unchanged — the lead's spawn prompt still passes
  the path.
- Otherwise the prompt — mode detection, tool budget, verdict schema —
  is preserved verbatim.

The original `agent/prompts/manager.md` becomes the canonical source
referenced by the agent definition (load-on-spawn via an `include`
directive if Agent Teams supports it, or duplicated and kept in sync
via a docs/architecture.md note if not — the dashboard's
`prompts/manager.md` viewer continues to surface the same content).

### Update the lead's spawn-team prompt

The lead's system prompt (currently in
`agent/prompts/roles/lead.md` or wherever the orchestrator threads it)
ends today with an instruction to spawn `backend`, `frontend`, and `qa`
teammates. Append:

> When backend, frontend, and qa report completion, assemble the review
> package (paths attached) and spawn a `manager` agent via the Agent
> tool. Pass the review package path and the verdicts file path in the
> spawn prompt, exactly as the bash `run_manager_review` did. Do not
> attempt to review the work yourself — the manager is the quality
> gate.

The exact spawn prompt mirrors the bash one
(`run-manager.sh:1936-1942`), so the manager's behaviour is unchanged:

```
Review the employee work package at: <path>

Write your verdicts to: <verdicts_file>

Your hard turn budget for this review is <N>. Treat turn <N/2> as your
soft deadline to start drafting the verdicts file.

Read the review package file first, then evaluate each project's work
against the criteria in your system prompt. Be strict on completeness
— never approve partial implementations.
```

### Delete the bash review path

In `agent/scripts/run-manager.sh`:

- Delete `run_manager_review` (lines 1885–~2000, including the
  heartbeat subshell at 1956–1962 and the stream parsing loop at
  1973+).
- Delete both calls — `verdicts_file=$(run_manager_review …)` at line
  3124 and the retry call at line 3268.
- Replace the call sites with: read the verdict JSON file produced by
  the in-session manager agent. The path is the same
  (`LOG_DIR/run-${RUN_ID}-verdicts.json`); the producer changes from
  external process to sibling agent.

### Retire `manager_heartbeat`

- Delete the heartbeat subshell in `run-manager.sh`.
- Remove `manager_heartbeat` from the dashboard webhook routes
  (`dashboard/backend/app/routers/webhook.py`) and the corresponding
  frontend event-type handlers.
- Update `services/stale_run_reaper.py` to drop any special-case for
  the manager-review window. The ordinary `last_event_at` heartbeat
  (introduced by the run-lifecycle overhaul) already covers the
  manager's stream activity now that it's on the main session.

### Token accounting

Today the manager's tokens are added separately by the bash EXIT trap:

- `run-manager.sh:3092` and `:3111` compute
  `tokens_total = _TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT`, where
  `_TOTAL_TOKENS_IN/OUT` are summed inside the bash manager-stream
  parser.

After this change the manager's `AssistantMessage.usage` flows through
the orchestrator's `handle_stream_event` (`station_orchestrator.py:1332-
1334`), which already increments `state.tokens_in` and
`state.tokens_out`. The bash addition becomes redundant and is deleted
along with the rest of `run_manager_review`. Token totals reported in
`run_complete` payloads are unchanged in value.

### Stream / log file

`run-${RUN_ID}-manager.stream.jsonl` is no longer produced. Operators
reading the manager's activity consult the orchestrator stream (already
written by `handle_stream_event`'s log-file branch at
`station_orchestrator.py:1319-1326`). Dashboard "Run Log" tab continues
to surface the unified stream.

### Revert PR #376

Once the heartbeat machinery is removed, PR #376's frontend / reaper
hooks are dead code. Open a follow-up revert PR (or include the
revert in #390's PR) covering:

- Bash heartbeat subshell.
- `manager_heartbeat` event handler in `routers/webhook.py`.
- Any frontend code rendering `manager_heartbeat` distinctly.
- The dashboard reaper's manager-review carve-out, if present.

## Acceptance criteria

Quoted from #390, expanded:

- [ ] **"Manager agent definition (`agent/agents/manager.md`) — already
      exists; ensure it's set up as Agent Teams sibling"** — file
      created (the issue body's claim of existence is incorrect; see
      Note above). Frontmatter present and parseable by the Agent
      Teams loader; body mirrors `agent/prompts/manager.md`.
- [ ] **"Lead's spawn-team prompt includes the manager"** — diff to
      the lead's system prompt visible in the PR; one paragraph added
      describing when and how to spawn the manager.
- [ ] **"Bash `run_manager_review` function deleted"** —
      `grep -n 'run_manager_review' agent/scripts/run-manager.sh`
      returns nothing.
- [ ] **"`manager_heartbeat` event type retired"** — `grep -rn
      'manager_heartbeat' agent/ dashboard/backend/ dashboard/frontend/`
      returns nothing.
- [ ] **"PR #376 reverted (heartbeat code removed from bash)"** —
      explicit revert commit or inline deletion; the bash subshell
      heartbeat loop is gone.
- [ ] **"Test: end-to-end run shows manager activity in the same
      stream as lead/teammates"** — a captured `stream.jsonl` from a
      live test run contains `AssistantMessage` blocks whose
      `parent_tool_use_id` (or agent attribution field) maps to the
      manager. Single file, no companion `manager.stream.jsonl`.
- [ ] **"Manager review tokens accounted for in the run's
      `tokens_total`"** — orchestrator's accumulated `tokens_in` /
      `tokens_out` after run completion equals the sum of the lead's,
      teammates', and manager's per-message `AssistantMessage.usage`.
      The previous bash addition is no longer needed and is removed
      without changing the reported total.

## Dependencies / blocks

- **Hard dependency**: Tier 1A. While the bash review path could be
  surgically deleted independently, the cleanest landing is on top of
  the Python-driven run loop where the lead's session lifecycle is
  already owned by the orchestrator.
- **Hard dependency**: Tier 1B (`ClaudeSDKClient`). The multi-turn
  pattern of "wait for specialists, then spawn manager" requires the
  client-with-send-and-receive lifecycle.
- **Soft synergy**: Issue #389 (inline audit). Once the manager runs in
  the same session, its tool calls flow through the same audit path
  for free.
- Blocks: Issue #391 (decompose long runs). Long runs become much
  easier to reason about once everything is on one stream and one
  process.

## Risks and rollback

- **Risk**: the manager agent's tool ACL (current: full VM access via
  no `--allowedTools`) differs from a sibling agent's default ACL.
  Mitigation: set `tools: Read, Edit, Write, Bash, Glob, Grep` (and
  whatever else the manager prompt actually exercises) in the new
  `manager.md` frontmatter to mirror the bash-time access pattern.
- **Risk**: the lead must be reliably instructed to spawn the manager
  *only* after the specialists finish. Mitigation: an explicit
  precondition in the lead's prompt ("only spawn the manager when
  every teammate has emitted a final report"). Verify in the live test
  run.
- **Risk**: total run turn budget exhaustion if the manager uses many
  turns. Mitigation: keep the soft-deadline pattern from the existing
  prompt (turn `N/2` is the "start writing verdicts" line). The hard
  ceiling becomes the run-level cap rather than a sub-cap.
- **Risk**: the manager produces tool calls that fail an autonomy
  policy and `can_use_tool` denies them. Mitigation: the manager
  prompt restricts itself to read-only `gh api` / `gh issue view`
  calls plus the single `Write` of the verdict file; the autonomy
  policy already permits these for `lead` / sibling agents.
- **Rollback**: revert the deletion of `run_manager_review` and the
  prompt-update commit. The new `agent/agents/manager.md` can stay in
  place unreferenced.

## Test strategy

- **Unit (pytest)**:
  - The lead's prompt rendering test asserts the manager-spawn
    paragraph is present and references the correct agent name.
  - The verdict file reader (post-review state machine) is exercised
    against a manager-produced verdict JSON to confirm the format is
    unchanged.
- **Integration**: extend `tests/test_run_lifecycle.py` with a fixture
  that drives a full run through to verdict, asserts no
  `manager_heartbeat` webhook fires, asserts the single
  `run-${RUN_ID}.stream.jsonl` file contains the manager's activity,
  and asserts `audit_log` has rows tagged `actor='manager'`.
- **Manual (live)**: trigger one production-shape run on the dev box.
  Expected: one stream file, no `manager.stream.jsonl`, no
  `manager_heartbeat` lines in `launcher.out`, verdict file present
  with correct schema.
- **Regression watch**: dashboard's run-detail page renders the manager
  phase visually contiguous with lead/teammate phases (no synthetic
  "manager started" gap).
