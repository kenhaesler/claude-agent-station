# `RunComplete` SDK Tool — Structured Completion Signal — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: [#385](https://github.com/kenhaesler/claude-agent-station/issues/385) — Tier 1 / Issue C of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)
**Depends on**: [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) (`ClaudeSDKClient` migration)

## Context

Today, `agent/station_orchestrator.py::_is_work_complete` (lines 1541–1554)
decides whether a run is finished by string-matching the lead agent's prose:

```python
def _is_work_complete(result_text: str) -> bool:
    if not result_text:
        return False
    if "issues_completed" in result_text and "issues_failed" in result_text:
        return True
    lower = result_text.lower()
    return any(phrase in lower for phrase in [
        "all teammates have completed",
        "all workers have completed",
        "final report",
        "final summary",
    ])
```

The contract is the LLM's word choice. The function has two consumers — the
inner-loop `break` at line 2099–2106 and the gate in `handle_stream_event` at
line 1500 — both of which can fire spuriously or fail to fire when the model
phrases its output differently from the corpus the heuristic was tuned against.

Recent failure modes:

- Lead writes "the work is done" → no phrase match → outer `max_reentries` loop
  keeps respawning the lead for another 6 cycles.
- Lead writes "summary: final" in a status update unrelated to completion →
  phrase match → orchestrator emits `orchestrator_complete` while the run is
  still in flight.
- Lead emits a turn-complete `ResultMessage` with an empty `result` field (the
  SDK's normal behaviour when the lead delegates and then has nothing else to
  say) → max-turns ceiling hit, run drags on for the full `max_turns` budget.

Worse, five independent signals must agree because none is authoritative:

1. SDK `ResultMessage(subtype="success")`
2. `_is_work_complete(result_text)` text match
3. Python's `orchestrator_complete` webhook
4. Bash's EXIT-trap `run_complete` (removed by #383)
5. `log_importer` scanning stream files

This issue collapses (1)–(3) into a single structured signal driven by the lead:
a `RunComplete` SDK tool. The lead must call it; calling it is the *only* way
the run terminates cleanly. Tool input becomes the authoritative verdict
payload, replacing manager-review prose parsing as well.

## Goals

- A structured, schema-validated completion signal authored by the lead agent
  via SDK tool-use.
- The orchestrator's inner loop exits when the `RunComplete` tool is observed —
  no text matching.
- The lead's system prompt explicitly contracts: "call `RunComplete` to end the
  run". Failing to call it is treated as a soft failure (the loop times out at
  `max_reentries`) rather than a phrase-match accident.
- `orchestrator_complete` webhook fires once, carrying the parsed tool input
  as the authoritative payload.
- Manager review consumes the structured `verdicts` array directly. Today's
  prose-parsing path becomes a fallback.

## Non-goals

- Removing all five completion signals immediately. Signal (4) is removed by
  #383. Signal (5) (`log_importer`) stays; it serves a different purpose
  (post-hoc forensic analysis of recorded stream logs). Signals (1)–(3)
  collapse here.
- Changing the dashboard's `orchestrator_complete` event contract on the wire,
  beyond adding a `verdicts` field. The existing `is_error`, `duration_ms`,
  `num_turns` fields stay.
- Schema migration for runs that already finished under the old heuristic —
  history is left as-is.

## Approach

### New module: `agent/tools/run_complete.py`

Create `agent/tools/` (package) with `__init__.py` and `run_complete.py`. The
file exports:

```python
from claude_agent_sdk import tool  # or whatever the SDK's decorator is named
from pydantic import BaseModel, Field

class _Verdict(BaseModel):
    project: str
    issue_number: int | None = None
    decision: Literal["APPROVE", "APPROVE_INTEGRATION", "PR", "REJECT", "SKIP"]
    reasoning: str | None = None
    branch: str | None = None
    base_branch: str | None = None

class RunCompleteInput(BaseModel):
    status: Literal["success", "partial", "blocked"]
    verdicts: list[_Verdict] = Field(default_factory=list)
    summary: str

RUN_COMPLETE_TOOL_NAME = "RunComplete"

@tool(name=RUN_COMPLETE_TOOL_NAME, input_schema=RunCompleteInput.model_json_schema())
async def run_complete_handler(input_dict: dict) -> dict:
    """Lead agent calls this to signal authoritative run completion.

    Returns a tool_result acknowledgement; the orchestrator inspects the same
    tool_use event via handle_stream_event and treats it as the completion
    contract.
    """
    try:
        payload = RunCompleteInput.model_validate(input_dict)
    except ValidationError as exc:
        return {"is_error": True, "content": f"RunComplete validation failed: {exc}"}
    return {"is_error": False, "content": f"Acknowledged: {payload.status}"}
```

The decorator's exact form depends on the SDK's tool-registration API; the
shape above is illustrative. The tool is included in `ClaudeAgentOptions`'s
allowed-tools list and registered on the same `ClaudeSDKClient` instance that
#384 introduces.

### Wiring in `agent/station_orchestrator.py`

1. `build_team_prompt` (currently `agent/station_orchestrator.py:731`) and
   `build_followup_prompt` (line 1012) gain a new authoritative-contract
   paragraph:

   ```
   When all teammates are done — or you cannot proceed further — call the
   `RunComplete` tool with a structured summary. This is the ONLY way to
   end the run cleanly. Do not announce "the work is done" in prose; the
   orchestrator does not read your prose for completion. Status values:
   - "success": all in-flight issues have a verdict.
   - "partial": some issues progressed, some did not (record the rest in
     `verdicts` with `decision: "SKIP"` and a reason).
   - "blocked": you cannot proceed without operator input.
   ```

2. `_StreamState` (line 65) gains a field:

   ```python
   run_complete_payload: dict | None = None
   ```

3. `handle_stream_event` (line 1314) intercepts `ToolUseBlock` whose
   `block.name == "RunComplete"`. Currently the block walker at lines 1349–
   1392 counts tool calls and emits narration; we add an explicit branch:

   ```python
   elif isinstance(block, ToolUseBlock):
       if state:
           state.tool_calls += 1
       if block.name == RUN_COMPLETE_TOOL_NAME:
           try:
               parsed = RunCompleteInput.model_validate(block.input)
           except ValidationError as exc:
               # Schema invalid; tool_result error will tell the lead to retry.
               logger.warning("RunComplete malformed: %s", exc)
               return
           if state is not None:
               state.run_complete_payload = parsed.model_dump()
           # Emit the single, authoritative orchestrator_complete webhook now,
           # carrying the parsed payload. ResultMessage emission later in the
           # stream becomes a no-op for this run.
           post_webhook(config, "orchestrator_complete", {
               "run_id": f"run-{run_id}",
               "is_error": False,
               "status": parsed.status,
               "verdicts": parsed.model_dump()["verdicts"],
               "summary": parsed.summary,
           })
   ```

4. The `ResultMessage` branch (lines 1461–1536) becomes a fallback: it fires
   `orchestrator_complete` only when `state.run_complete_payload is None`.
   That preserves behaviour for any code path where the lead exits without
   ever calling the tool.

5. The inner orchestration loop (`agent/station_orchestrator.py:2047` after
   #384's rewrite) exits not on `_is_work_complete(...)` but on:

   ```python
   if state.run_complete_payload is not None:
       work_complete = True
       break
   ```

   The `_is_work_complete` import remains as a deprecated fallback for one
   release window, then is removed alongside the function.

6. `_is_work_complete` (lines 1541–1554) is **deleted** at the end of the
   PR. The two call sites are updated to read `state.run_complete_payload`.

### Manager review consumption

`agent/manager_review.py` (created by #383) currently parses the lead's
final-message prose for verdicts. After #385, it reads
`state.run_complete_payload["verdicts"]` first; falls back to prose parsing
only if absent. The structured path is preferred because each `Verdict`
dataclass field (project, issue_number, decision, reasoning) maps 1:1 onto
`agent/verdict_execution.Verdict.from_dict` (`agent/verdict_execution.py:66`).

### Schema validation and retry

If the lead calls `RunComplete` with malformed input (missing `status`, bad
`decision` enum, etc.), the handler returns a tool_result with `is_error=True`
and an explanatory `content`. The SDK delivers this back to the lead, which
can retry with the corrected payload. The orchestrator does **not** terminate
on malformed input — only a successful validated call latches
`run_complete_payload`.

### Backward compatibility window

For one release after #385 lands, both signals are honoured:

- `state.run_complete_payload is not None` → primary completion.
- `_is_work_complete(result_text)` → fallback, logs a warning that the lead
  did not call the tool.

The fallback warning is the metric we watch in staging: once the rate hits
zero across two consecutive runs, the fallback is removed in a follow-up PR.

## Acceptance criteria

Lifted from the issue body, expanded:

- [ ] **`agent/tools/run_complete.py`: SDK tool definition + handler.** File
  exists; exports `RUN_COMPLETE_TOOL_NAME`, `RunCompleteInput` (pydantic),
  and an async handler registered via the SDK's tool decorator. Module
  imports cleanly and the schema round-trips through `model_validate` ↔
  `model_dump`.
- [ ] **Lead's system prompt updated: completion requires `RunComplete` tool
  call.** `build_team_prompt` and `build_followup_prompt` both include the
  authoritative-contract paragraph above. A unit test asserts both prompts
  contain the substring `RunComplete`.
- [ ] **`_is_work_complete` deleted.** Function removed from
  `agent/station_orchestrator.py`; all call sites updated. CI grep job
  asserts no remaining references in `agent/`.
- [ ] **`handle_stream_event` detects the tool call and emits
  `orchestrator_complete` with the parsed input.** Inspect tool-use blocks
  whose `name == "RunComplete"`, validate against the schema, latch the
  payload onto `_StreamState`, emit the webhook exactly once.
- [ ] **Schema validation: malformed input → tool error back to lead, retry.**
  Validation errors return `{"is_error": True, "content": ...}` from the
  handler; the orchestrator does not exit on a malformed call. Unit test
  asserts that the lead can call the tool again after a malformed first
  attempt and the second-call latch wins.
- [ ] **Test: `dashboard/backend/tests/test_run_complete_tool.py` covers
  happy path + malformed input + missing required fields.** Tests below.

## Dependencies / blocks

- **Depends on**: [#384](https://github.com/kenhaesler/claude-agent-station/issues/384).
  The tool registers on `ClaudeSDKClient`; that lifecycle has to exist first.
- **Builds on**: [#383](https://github.com/kenhaesler/claude-agent-station/issues/383).
  Once bash is gone, the manager-review path is single-language and the
  structured `verdicts` array flows straight into `verdict_execution.execute()`
  with no prose parsing in the middle.
- **Eliminates**:
  - `_is_work_complete` heuristic (`agent/station_orchestrator.py:1541`).
  - The work-complete-gate logic from PR #381 (commit `11d78de`).
  - The text-matching ambiguity that caused turns=31 empty-result loops.

## Risks and rollback

| Risk | Mitigation |
|---|---|
| Lead "forgets" to call `RunComplete` and the run hits `max_reentries`. | The fallback heuristic stays for one release window. Track the "lead-did-not-call-RunComplete" warning rate as a release-gate metric; remove fallback once zero. |
| Malformed tool calls flood the loop in a cycle (lead retries indefinitely). | Cap retries: if three consecutive malformed `RunComplete` calls land in a single iteration, treat that as a failure, log loudly, and let the outer `max_reentries` loop run its natural course. |
| The SDK's tool-registration API differs from the illustrative `@tool` decorator above. | Read `claude_agent_sdk.tools` module before implementation; adapt the registration form. The orchestrator-side observation of tool-use blocks (`isinstance(block, ToolUseBlock)`) is unaffected. |
| Pydantic dep is not currently used in `agent/`. | Pydantic is already an indirect dep via FastAPI in the dashboard backend. Add an explicit pin in `agent/requirements.txt` (or wherever the agent's deps live) in this PR. Alternative: hand-write the validator with `typing.TypedDict` + runtime checks; defer the choice to implementation. |
| Webhook ordering: `RunComplete` tool fires *before* the SDK's `ResultMessage`. | The fallback gate (`state.run_complete_payload is None`) in the `ResultMessage` branch prevents a duplicate emission. Test asserts exactly one `orchestrator_complete` per run. |

**Rollback**: revert the PR. The pre-#385 `_is_work_complete` heuristic is
restored. The new tool, system-prompt text, and `RunComplete` registration
disappear in one revert. No data migration needed.

## Test strategy

### Unit (`dashboard/backend/tests/test_run_complete_tool.py`)

- `test_handler_valid_input_returns_ack`: feed a valid dict to
  `run_complete_handler`; assert `is_error` is False and `content` echoes
  status.
- `test_handler_missing_status_returns_error`: feed `{"verdicts": []}` (no
  status); assert `is_error` is True and `content` mentions the field.
- `test_handler_invalid_decision_returns_error`: feed
  `verdicts=[{"project":"foo","decision":"MAYBE"}]`; assert validation
  error includes "decision".
- `test_handler_unknown_keys_ignored_or_rejected`: pin behaviour explicitly
  (pydantic's default is to ignore; this test locks it in).
- `test_streamstate_latches_payload`: fabricate a `ToolUseBlock` with the
  RunComplete payload, drive `handle_stream_event`, assert
  `state.run_complete_payload` is set.
- `test_orchestrator_complete_emitted_once`: drive the same fake stream
  through `handle_stream_event` *and* a subsequent `ResultMessage`; assert
  the `post_webhook` mock observed exactly one `orchestrator_complete`.
- `test_retry_after_malformed`: send a malformed tool call, then a valid
  one; assert second call latches.

### Integration (`dashboard/backend/tests/test_orchestrator_clientsdk.py`,
created in #384, extended here)

- `test_inner_loop_exits_on_run_complete`: drive a fake `ClaudeSDKClient`
  whose stream contains an `AssistantMessage` carrying a `RunComplete`
  tool-use block. Assert the inner `async for` exits, `work_complete=True`,
  the outer loop terminates after one iteration.
- `test_fallback_when_tool_not_called`: drive a fake client whose stream
  emits the lead saying "final summary" but never calling the tool. Assert
  the legacy `_is_work_complete` fallback still terminates the run **and**
  logs the fallback warning.

### Smoke

- Live Agent Teams session against the standard 2-issue sandbox fixture.
  Assert the lead's first `ResultMessage` follows a `RunComplete` tool call
  (i.e., the lead actually uses the new contract). Assert
  `orchestrator_complete` webhook payload contains a non-empty `verdicts`
  array that matches the issue numbers in the queue.

### Manual

- Manually trigger a run that the lead will struggle to finish (an
  intentionally vague issue) and verify the lead either calls `RunComplete`
  with `status="blocked"` or the `max_reentries` ceiling kicks in cleanly.
  No phrase-match accidents either way.

## Notes / open questions

- **Tool input shape.** The issue body's schema is illustrative; the spec
  above expands `verdicts[]` into a typed object that maps onto
  `agent/verdict_execution.Verdict`. Confirm against
  `verdict_execution.Verdict.from_dict` (`agent/verdict_execution.py:66`)
  during implementation.
- **Tool return value contract.** The SDK's tool-use protocol expects a
  `tool_result` with `content` and `is_error`. The handler returns dict
  in that shape; verify against SDK source.
- **Tier 3 #ISSUE_T3B (manager-review port) interaction.** Manager review
  is the obvious downstream consumer of `verdicts`. If T3B has not landed,
  the fallback prose-parsing path in manager review still works because
  the lead's `summary` field is human-readable. T3B's eventual rewrite
  can short-circuit to the structured array.

> **Note**: The illustrative `@tool` decorator above is a placeholder for
> whatever shape `claude_agent_sdk.tools` actually exposes. The behaviour
> the orchestrator depends on — observing `ToolUseBlock(name="RunComplete")`
> on the message stream — is contract-stable regardless of the registration
> form.
