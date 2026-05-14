# Inline Audit Writes from Stream — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: #389 (Tier 3 / A of epic #382)
**Depends on**: Tier 1B — `ClaudeSDKClient` migration

## Context

`agent/audit_hook.py` registers two SDK hook callbacks — `PreToolUse`
(via `make_pre_tool_hook`) and `PostToolUse` (via `make_post_tool_hook`)
— and the orchestrator wires them into every `ClaudeAgentOptions` it
constructs (`agent/station_orchestrator.py:2028` for the lead and
`agent/conflict_resolver/sdk_runner.py:66` for the conflict-resolver
session). Each registered callback writes one row into the dashboard's
`audit_log` table: a `status='started'` row on `PreToolUse`, then an
`UPDATE` setting `status='ok'|'error'`, `exit_code`, `stdout_tail`, and
`stderr_tail` on `PostToolUse`. Rows are matched by `idempotency_key`
(= SDK `tool_use_id`).

This mechanism is structurally fragile. PR #381's post-mortem traced a
recurring symptom — audit log going silent ~2 minutes into a long
session — to `cli.js:7552 sendRequest` raising `Error: Stream closed`
when the bundled CLI's stdin-close countdown fires. Every subsequent
hook callback the CLI tries to deliver to the Python side raises; the
Python coroutine never runs, the `audit_log` never gets the `finished`
update (or the `started` row), and `audit_hook._record_hook_failure`
increments `_HOOK_CB_FAILURE_COUNT` and emits one `[hook-cb-fail]`
warning per missed callback. The orchestrator surfaces the delta to the
dashboard at session-end via the `get_hook_callback_failure_count()`
counter and `audit_dropouts` webhook, but the audit data is irrecoverable.

PR #381 fixed the stdin-close root cause for the lead's main session,
but the design dependency remains: every tool call's audit row is
mediated by an SDK control-protocol round-trip, the protocol is
implemented in the third-party CLI we ship, and an SDK upgrade can
change hook semantics. The orchestrator *already* iterates the assistant
message stream (`async for message in client.receive_response()` after
Tier 1B); every `AssistantMessage` already contains `ToolUseBlock`
items, and every `UserMessage` carries `ToolResultBlock` items with the
matching `tool_use_id`. We have all the data we need without the
control-protocol detour.

This spec replaces the hook-callback path with stream-derived audit
writes performed inline in `handle_stream_event`. The
`can_use_tool` callback path (used for autonomy-level policy enforcement
and the `agent_events.auto_mode_decision` rows) is retained — it is a
different SDK mechanism with a different failure profile.

## Goals

- Every tool invocation in a run produces exactly one `audit_log` row
  with both `status='started'` and a terminal status (`ok` / `error`)
  populated.
- Zero `[hook-cb-fail]` warnings during a normal run; zero audit
  dropouts attributable to SDK stdin-close races.
- No new SQLite contention: writes happen on the orchestrator's event
  loop thread but are offloaded via `asyncio.to_thread` so the event
  loop stays responsive.
- Existing `audit_log` schema, columns, and `idempotency_key` semantics
  are unchanged. Dashboard timeline / per-tool drill-downs continue to
  work without changes.

## Non-goals

- Replacing the `can_use_tool` callback. It runs synchronously *before*
  the tool executes and is the only mechanism the SDK exposes for
  policy-based denial. It writes to `agent_events`, not `audit_log`.
- Migrating older runs' audit data. The change is forward-only.
- Adding new columns to `audit_log`. The stream-derived writes populate
  the same columns the hook callbacks did.

## Approach

### New helper: `write_audit_started_from_stream`

Add a thin wrapper to `agent/audit_hook.py` that owns the stream-derived
write path. It reuses `write_audit_start` and `write_audit_finish` (the
DB writers already used by the hook callbacks) but accepts the
already-parsed block fields rather than the SDK's hook-callback dict:

```python
def write_audit_started_from_block(
    *, run_id: str, actor: str, block: ToolUseBlock,
    trace_id: str | None = None, db_path: str | None = None,
) -> None: ...

def write_audit_finished_from_block(
    *, block: ToolResultBlock, db_path: str | None = None,
) -> None: ...
```

Both wrappers route to `asyncio.to_thread` for the actual SQLite write
so the orchestrator event loop is not blocked under WAL contention
(matches today's hook-callback behaviour at `audit_hook.py:374` /
`:406`).

### Orchestrator wiring

`agent/station_orchestrator.py::handle_stream_event` currently walks
`AssistantMessage.content` and emits `narration` / counts tool calls
(`station_orchestrator.py:1347-1391`). Extend that loop to call
`write_audit_started_from_block` immediately after the existing
`state.tool_calls += 1` increment for each `ToolUseBlock`:

```python
elif isinstance(block, ToolUseBlock):
    if state:
        state.tool_calls += 1
    logger.info("Lead agent tool call: %s", block.name)
    await asyncio.to_thread(
        write_audit_started_from_block,
        run_id=run_id,
        actor=_actor_for_block(message),  # lead vs teammate-<id>
        block=block,
    )
    ...
```

Two structural changes follow:

1. `handle_stream_event` becomes `async`. Its caller at
   `station_orchestrator.py:2079` is already inside an `async for`, so
   `await handle_stream_event(...)` is a single-character delta.
2. `UserMessage` handling gains a new branch. The SDK delivers tool
   results as `ToolResultBlock` items inside `UserMessage.content`. Walk
   those blocks and call `write_audit_finished_from_block` for each.
   `ToolResultBlock` carries `tool_use_id`, `content`, and `is_error` —
   the same fields `_extract_outcome` already digests from the hook's
   `tool_response` dict.

### Actor attribution

Today the hook callback inspects `input_data["agent_id"]` (populated by
the SDK when a tool call originates inside a sub-agent / teammate
context) and prefixes the row's `actor` with `teammate-` when present
(`audit_hook.py:366-370`). The equivalent in the stream path comes from
the message's containing session: Agent Teams sub-agents emit their tool
calls on the parent stream tagged with a `parent_tool_use_id` or
`agent_id`. The orchestrator's `state` already disambiguates lead vs
teammate sessions; surface that on the stream-state object and pass it
to the wrapper as `actor`.

If the SDK doesn't expose sub-agent attribution on the
`AssistantMessage` itself, fall back to inspecting the message's
`parent_tool_use_id`: a non-null value means the message originated
inside a sub-agent. The exact field is implementation-defined; verify
against `claude_agent_sdk._internal.message_parser` in the
implementation PR and add a single targeted test asserting the
attribution.

### Delete the hook factories

Once the orchestrator-side wiring is in place and tested:

- `agent/audit_hook.py::make_pre_tool_hook` — delete.
- `agent/audit_hook.py::make_post_tool_hook` — delete.
- `agent/audit_hook.py::_record_hook_failure` — delete.
- `agent/audit_hook.py::_HOOK_CB_FAILURE_COUNT` and
  `get_hook_callback_failure_count` — delete.
- `agent/station_orchestrator.py:50-51` — drop the imports.
- `agent/station_orchestrator.py:2028-2040` — drop the
  `hooks={"PreToolUse": [...], "PostToolUse": [...]}` block from
  `ClaudeAgentOptions`.
- Same deletion in `agent/conflict_resolver/sdk_runner.py:22-23, :66`.
- Any caller of `get_hook_callback_failure_count()` (audit_dropouts
  webhook plumbing, dashboard counters) — delete the dead code path.

### Keep the policy / decision audit writer

`make_audited_policy` (`audit_hook.py:147`) and `write_decision_event`
stay. They write to `agent_events` (`auto_mode_decision` rows), not
`audit_log`, and the `can_use_tool` callback path is unaffected by the
stdin-close race because it's a synchronous request/response
between the CLI and the Python side that completes before any tool
runs.

## Acceptance criteria

Quoted from #389, expanded:

- [ ] **"`agent/audit_hook.py` deletes the PreToolUse / PostToolUse hook
      factories"** — `make_pre_tool_hook`, `make_post_tool_hook`,
      `_record_hook_failure`, `_HOOK_CB_FAILURE_COUNT`, and
      `get_hook_callback_failure_count` removed. `grep -rn
      'PreToolUse\|PostToolUse' agent/` returns nothing.
- [ ] **"`agent/station_orchestrator.py::handle_stream_event` writes
      audit_log rows directly from `ToolUseBlock` and tool-result
      events"** — `ToolUseBlock` and `ToolResultBlock` branches in
      `handle_stream_event` each call the new stream-derived writer.
- [ ] **"`can_use_tool` callback path stays"** —
      `make_audited_policy` and its callers are unchanged.
      `auto_mode_decision` rows continue to be written for every tool
      call regardless of autonomy level.
- [ ] **"Test: simulated stream with 5 tool calls → 5 audit_log rows,
      no SDK hook registration"** — `pytest` exercises
      `handle_stream_event` with a hand-built sequence of
      `AssistantMessage` / `UserMessage` mocks. After the sequence,
      `SELECT COUNT(*) FROM audit_log WHERE run_id = ?` returns 5, and
      every row has both `started_at` and `finished_at` populated.

## Dependencies / blocks

- **Hard dependency**: Tier 1B (`ClaudeSDKClient` migration). The new
  client's `receive_response()` is the iteration point this design
  builds on; calling pattern is otherwise tied to the legacy `query()`
  generator.
- **Soft dependency**: Issue #390 (manager-as-sibling). When the manager
  becomes a sibling agent in the same SDK session, its tool calls
  automatically flow through this same audit path — no separate
  per-process audit wiring needed for the manager. Either issue can
  land first.
- Blocks: removing the `[hook-cb-fail]` warning from operator
  documentation and the dashboard's `audit_dropouts` counter.

## Risks and rollback

- **Risk**: lost messages mid-stream produce missing audit rows. The
  SDK can buffer-coalesce messages in error paths; if a `UserMessage`
  carrying a `ToolResultBlock` is dropped, the corresponding row stays
  in `started` state forever. Mitigation: keep the existing
  `started_at`-only sentinel value and reuse the dashboard's stale-
  audit-row monitor (already present for the old hook-callback failure
  mode).
- **Risk**: the per-tool-call `asyncio.to_thread(sqlite3...)` introduces
  contention at the orchestrator event loop. Mitigation: this is
  *strictly less* contention than today (one thread call per tool call,
  same as `make_pre_tool_hook` does at `audit_hook.py:374`). No
  regression expected.
- **Risk**: actor attribution regresses (lead rows misattributed to
  teammates or vice versa). Mitigation: explicit test asserting both
  paths produce the correct `actor` value.
- **Rollback**: revert the orchestrator wiring and the deletion in one
  commit. The new stream-derived writers can stay in `audit_hook.py`
  unreferenced — they're pure functions.

## Test strategy

- **Unit (pytest)**:
  - Hand-built `AssistantMessage` / `UserMessage` sequence with five
    tool calls; assert `audit_log` row count and statuses.
  - `ToolUseBlock` from a sub-agent (set `parent_tool_use_id`) → row's
    `actor` is `teammate-<id>`.
  - `ToolResultBlock` with `is_error=True` → row's `status='error'`
    and `stderr_tail` populated.
- **Integration**: extend `tests/test_run_lifecycle.py` (or sibling)
  with one end-to-end fixture that drives a fake SDK session through
  the orchestrator, asserts the audit_log shape, and verifies no
  `[hook-cb-fail]` warning is emitted.
- **Manual**: trigger one full Agent Teams run on the dev box, query
  `SELECT COUNT(*) FROM audit_log WHERE run_id = ? AND finished_at IS
  NULL` — must return zero on a healthy run.
- **Regression watch**: dashboard's `audit_log_completeness_ratio`
  metric (if present) should converge to 1.0 across all post-deploy
  runs.
