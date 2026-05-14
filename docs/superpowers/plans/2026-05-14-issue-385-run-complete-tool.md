# `RunComplete` SDK Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prose-matching `_is_work_complete` heuristic with a structured SDK tool (`RunComplete`) that the lead agent calls to authoritatively signal run completion, carrying a validated `verdicts` array that flows straight into manager review / verdict execution.

**Architecture:** A new in-process MCP server (`agent/tools/run_complete.py`) registers a single `RunComplete` tool via `claude_agent_sdk.tool` + `create_sdk_mcp_server`. The orchestrator wires this server into `ClaudeAgentOptions.mcp_servers`, adds `mcp__run_complete__RunComplete` to `allowed_tools`, and extends `build_team_prompt` / `build_followup_prompt` with the authoritative-contract paragraph. `_StreamState` gains a `run_complete_payload` field. `handle_stream_event`'s `ToolUseBlock` branch checks `block.name == "RunComplete"`, validates input against the pydantic schema, latches the payload, and emits the single authoritative `orchestrator_complete` webhook with the parsed `verdicts`. The inner `async for` exits when the payload is latched; the legacy `_is_work_complete` path remains as a fallback for one release.

**Tech Stack:** Python 3.11+, `claude_agent_sdk` (`tool`, `create_sdk_mcp_server`), `pydantic` (already a transitive dep via FastAPI), `pytest` + `pytest-asyncio`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `agent/tools/__init__.py` | **New** — empty package marker. |
| `agent/tools/run_complete.py` | **New** — defines `RunCompleteInput` (pydantic), `RUN_COMPLETE_TOOL_NAME`, the `@tool`-decorated async handler `run_complete_handler`, and a `build_run_complete_server()` helper returning the `McpSdkServerConfig` to pass into `ClaudeAgentOptions.mcp_servers`. |
| `agent/station_orchestrator.py` | Add `run_complete_payload: dict | None = None` to `_StreamState` (line 66); extend the `ToolUseBlock` branch in `handle_stream_event` (lines 1354–1391) to detect `RunComplete` and latch / emit; gate the existing `ResultMessage` `orchestrator_complete` emission on `state.run_complete_payload is None`; rewrite the inner-loop exit (`async for message in client.receive_response()` from #384) to break when `state.run_complete_payload is not None`; extend `build_team_prompt` (line 731) and `build_followup_prompt` (line 1012) with the contract paragraph; wire the new MCP server into `ClaudeAgentOptions`. |
| `dashboard/backend/tests/test_run_complete_tool.py` | **New** — handler unit tests + stream-state latch tests + retry-after-malformed test. |
| `dashboard/backend/tests/test_orchestrator_clientsdk.py` | Extend with `test_inner_loop_exits_on_run_complete` and `test_fallback_when_tool_not_called`. |

---

## Tasks

### Task 1 — Create `agent/tools/run_complete.py` with the pydantic schema and the tool handler

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_run_complete_tool.py`:

```python
"""Tests for agent.tools.run_complete (issue #385)."""
from __future__ import annotations

import asyncio
import pytest


def test_run_complete_input_validates_happy():
    from agent.tools.run_complete import RunCompleteInput

    payload = {
        "status": "success",
        "verdicts": [
            {"project": "owner/repo", "issue_number": 1, "decision": "APPROVE",
             "reasoning": "tests pass", "branch": "autonomous/issue-1",
             "base_branch": "main"},
        ],
        "summary": "All issues resolved.",
    }
    parsed = RunCompleteInput.model_validate(payload)
    assert parsed.status == "success"
    assert len(parsed.verdicts) == 1
    assert parsed.verdicts[0].decision == "APPROVE"


def test_run_complete_input_rejects_missing_status():
    from agent.tools.run_complete import RunCompleteInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunCompleteInput.model_validate({"verdicts": [], "summary": "no status"})


def test_run_complete_input_rejects_unknown_decision():
    from agent.tools.run_complete import RunCompleteInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunCompleteInput.model_validate({
            "status": "success",
            "verdicts": [{"project": "owner/repo", "decision": "MAYBE"}],
            "summary": "x",
        })


def test_handler_returns_ack_for_valid_input():
    from agent.tools.run_complete import run_complete_handler

    result = asyncio.run(run_complete_handler.handler({
        "status": "success",
        "verdicts": [],
        "summary": "done",
    }))
    assert result.get("is_error", False) is False
    # MCP-shaped content list with at least one text block.
    contents = result.get("content", [])
    assert any(c.get("type") == "text" for c in contents)


def test_handler_returns_error_for_malformed_input():
    from agent.tools.run_complete import run_complete_handler

    result = asyncio.run(run_complete_handler.handler({"verdicts": []}))  # missing status + summary
    assert result.get("is_error") is True


def test_tool_name_constant_matches_decorator():
    from agent.tools.run_complete import RUN_COMPLETE_TOOL_NAME, run_complete_handler
    assert RUN_COMPLETE_TOOL_NAME == "RunComplete"
    # The SdkMcpTool object exposes .name on its dataclass.
    assert run_complete_handler.name == RUN_COMPLETE_TOOL_NAME


def test_build_run_complete_server_returns_mcp_config():
    from agent.tools.run_complete import build_run_complete_server

    server_config = build_run_complete_server()
    # McpSdkServerConfig dict shape (per claude_agent_sdk __init__).
    assert server_config["type"] == "sdk"
    assert server_config["name"] == "run_complete"
```

**Step 2: Run the test — confirm it fails.**

```
$ cd /home/simon/Documents/claude-agent-station
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v
```

Expected: 7 collection / import failures (`agent.tools.run_complete` does not exist).

**Step 3: Implementation — create the package and the tool module.**

Create `agent/tools/__init__.py` as an empty file:

```python
"""SDK tools registered with the Claude Agent SDK."""
```

Create `agent/tools/run_complete.py`:

```python
"""RunComplete SDK tool — structured completion signal for the lead agent.

The lead agent calls this tool to authoritatively end an Agent Teams run.
Tool input is schema-validated against ``RunCompleteInput``. Observation of
the resulting ``ToolUseBlock`` in the orchestrator stream is what triggers
the single ``orchestrator_complete`` webhook (#385). The prose-matching
``_is_work_complete`` heuristic survives for one release as a fallback.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from claude_agent_sdk import tool, create_sdk_mcp_server


RUN_COMPLETE_TOOL_NAME = "RunComplete"


class _Verdict(BaseModel):
    """One per-issue verdict in the RunComplete payload."""

    project: str
    issue_number: int | None = None
    decision: Literal["APPROVE", "APPROVE_INTEGRATION", "PR", "REJECT", "SKIP"]
    reasoning: str | None = None
    branch: str | None = None
    base_branch: str | None = None


class RunCompleteInput(BaseModel):
    """Pydantic schema validating the tool call payload."""

    status: Literal["success", "partial", "blocked"]
    verdicts: list[_Verdict] = Field(default_factory=list)
    summary: str


# JSON-schema dict for ClaudeSDKClient registration. Built from the pydantic
# model so the two stay in lock-step.
_RUN_COMPLETE_JSON_SCHEMA: dict = RunCompleteInput.model_json_schema()


@tool(
    name=RUN_COMPLETE_TOOL_NAME,
    description=(
        "Authoritatively end an Agent Teams run. The lead agent calls this "
        "tool when all teammates are done — or when the lead cannot proceed "
        "further. Status values: success | partial | blocked. The verdicts "
        "array carries one entry per issue. Calling this tool is the ONLY "
        "way to end the run cleanly; prose like 'final summary' is no "
        "longer detected."
    ),
    input_schema=_RUN_COMPLETE_JSON_SCHEMA,
)
async def run_complete_handler(args: dict) -> dict:
    """Tool handler invoked by the SDK when the lead calls RunComplete.

    Returns a tool_result-shaped dict. Schema-invalid input yields
    ``is_error=True`` so the lead can retry. Schema-valid input yields an
    acknowledgement; the orchestrator side independently observes the same
    tool_use block via handle_stream_event and treats it as the
    authoritative completion signal.
    """
    try:
        payload = RunCompleteInput.model_validate(args)
    except ValidationError as exc:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": f"RunComplete validation failed: {exc}"}],
        }
    return {
        "is_error": False,
        "content": [{"type": "text", "text": f"Acknowledged: {payload.status}"}],
    }


def build_run_complete_server():
    """Return the McpSdkServerConfig to pass into ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server(
        name="run_complete",
        version="1.0.0",
        tools=[run_complete_handler],
    )
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v
```

Expected:

```
PASSED ... test_run_complete_input_validates_happy
PASSED ... test_run_complete_input_rejects_missing_status
PASSED ... test_run_complete_input_rejects_unknown_decision
PASSED ... test_handler_returns_ack_for_valid_input
PASSED ... test_handler_returns_error_for_malformed_input
PASSED ... test_tool_name_constant_matches_decorator
PASSED ... test_build_run_complete_server_returns_mcp_config
```

**Step 5: Commit.**

```
$ git add agent/tools/__init__.py agent/tools/run_complete.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "feat(tools): add RunComplete SDK tool with pydantic-validated input"
```

---

### Task 2 — Add `run_complete_payload` to `_StreamState`

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_streamstate_has_run_complete_payload_field():
    """_StreamState gains run_complete_payload (defaults to None)."""
    from agent.station_orchestrator import _StreamState
    state = _StreamState()
    assert hasattr(state, "run_complete_payload"), (
        "_StreamState must expose run_complete_payload (issue #385)"
    )
    assert state.run_complete_payload is None


def test_streamstate_can_latch_payload():
    from agent.station_orchestrator import _StreamState
    state = _StreamState()
    state.run_complete_payload = {"status": "success", "verdicts": [], "summary": "done"}
    assert state.run_complete_payload["status"] == "success"
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_streamstate_has_run_complete_payload_field -v
```

Expected:

```
FAILED ... AssertionError: _StreamState must expose run_complete_payload ...
```

**Step 3: Implementation — add the field.**

In `agent/station_orchestrator.py`, locate the `@dataclass class _StreamState:` block (line 65). Add a new field at the end:

```python
@dataclass
class _StreamState:
    """Accumulates stream data for batched webhook delivery."""
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    turns: int = 0
    last_webhook_time: float = 0.0
    BATCH_INTERVAL: float = 15.0
    main_session_id: str | None = None
    # #385: Latched when the lead calls the RunComplete SDK tool. None until
    # the tool fires; once set, handle_stream_event suppresses the legacy
    # ResultMessage-driven orchestrator_complete emission, and the inner
    # orchestrate loop breaks at the next iteration boundary.
    run_complete_payload: dict | None = None
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_streamstate_has_run_complete_payload_field dashboard/backend/tests/test_run_complete_tool.py::test_streamstate_can_latch_payload -v
```

Expected: 2 passes.

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "feat(orchestrator): add run_complete_payload field to _StreamState"
```

---

### Task 3 — Detect `RunComplete` in `handle_stream_event`'s `ToolUseBlock` branch

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_handle_stream_event_latches_run_complete_payload(monkeypatch):
    """A ToolUseBlock with name='RunComplete' latches the parsed payload onto state."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")

    monkeypatch.setattr("agent.station_orchestrator.post_webhook", lambda *a, **k: None)

    tool_use = ToolUseBlock(
        id="tu-1",
        name="RunComplete",
        input={
            "status": "success",
            "verdicts": [
                {"project": "owner/repo", "issue_number": 1, "decision": "APPROVE",
                 "branch": "autonomous/issue-1", "base_branch": "main", "reasoning": "ok"},
            ],
            "summary": "Done.",
        },
    )
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    assert state.run_complete_payload is not None
    assert state.run_complete_payload["status"] == "success"
    assert state.run_complete_payload["verdicts"][0]["decision"] == "APPROVE"


def test_handle_stream_event_ignores_malformed_run_complete(monkeypatch):
    """A malformed RunComplete tool call does NOT latch the payload."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    monkeypatch.setattr("agent.station_orchestrator.post_webhook", lambda *a, **k: None)

    # Missing required 'status' and 'summary' fields.
    tool_use = ToolUseBlock(id="tu-bad", name="RunComplete", input={"verdicts": []})
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    assert state.run_complete_payload is None, (
        "Malformed RunComplete must not latch the payload — lead should retry"
    )


def test_handle_stream_event_orchestrator_complete_emitted_with_verdicts(monkeypatch):
    """Once the payload latches, an orchestrator_complete webhook fires with the verdicts."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")

    captured: list[tuple] = []
    monkeypatch.setattr(
        "agent.station_orchestrator.post_webhook",
        lambda config, event, payload: captured.append((event, payload)),
    )

    tool_use = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    msg = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(msg, "session_id", "sess-1")

    handle_stream_event(msg, config={}, run_id="run-x", log_file=None, state=state)

    events = [e for (e, _p) in captured]
    assert "orchestrator_complete" in events, "RunComplete must emit orchestrator_complete"
    payload = next(p for (e, p) in captured if e == "orchestrator_complete")
    assert payload["status"] == "success"
    assert payload["summary"] == "done"
    assert "verdicts" in payload
```

**Step 2: Run the tests — confirm they fail.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_handle_stream_event_latches_run_complete_payload dashboard/backend/tests/test_run_complete_tool.py::test_handle_stream_event_ignores_malformed_run_complete dashboard/backend/tests/test_run_complete_tool.py::test_handle_stream_event_orchestrator_complete_emitted_with_verdicts -v
```

Expected: all three fail because `handle_stream_event` currently does nothing special with `RunComplete` tool calls.

**Step 3: Implementation — extend `handle_stream_event`'s `ToolUseBlock` branch.**

In `agent/station_orchestrator.py`, find the `elif isinstance(block, ToolUseBlock):` block inside `handle_stream_event` (currently around lines 1354–1365). Add a `RunComplete` branch:

```python
                elif isinstance(block, ToolUseBlock):
                    if state:
                        state.tool_calls += 1
                    logger.info("Lead agent tool call: %s", block.name)
                    if block.name == "RunComplete":
                        from agent.tools.run_complete import RunCompleteInput
                        from pydantic import ValidationError
                        try:
                            parsed = RunCompleteInput.model_validate(block.input or {})
                        except ValidationError as exc:
                            # Schema-invalid input — the tool handler's tool_result
                            # already tells the lead to retry. Do NOT latch.
                            logger.warning("RunComplete malformed: %s", exc)
                        else:
                            if state is not None and state.run_complete_payload is None:
                                state.run_complete_payload = parsed.model_dump()
                                # #385: this is the authoritative
                                # orchestrator_complete emission. The fallback
                                # branch in the ResultMessage path is gated
                                # below on state.run_complete_payload being None.
                                post_webhook(config, "orchestrator_complete", {
                                    "run_id": f"run-{run_id}",
                                    "is_error": False,
                                    "status": parsed.status,
                                    "verdicts": [v.model_dump() for v in parsed.verdicts],
                                    "summary": parsed.summary,
                                    "duration_ms": 0,
                                    "num_turns": state.turns,
                                })
                    if pending_narration:
                        post_webhook(config, "narration", {
                            "run_id": f"run-{run_id}",
                            "agent_name": "Lead",
                            "narration": pending_narration[:500],
                            "narration_kind": "directive",
                        })
                        pending_narration = None
```

(The dict-fallback branch below it that also counts `tool_use` blocks doesn't need a `RunComplete` parallel — the SDK delivers structured `ToolUseBlock` instances; the dict fallback exists for raw passthrough cases the production SDK does not generate.)

**Step 4: Run the tests — confirm they pass.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v
```

Expected: all 10 tests pass (the 7 from Task 1 plus the 3 added here).

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "feat(orchestrator): latch RunComplete tool-use and emit authoritative orchestrator_complete"
```

---

### Task 4 — Gate the legacy `ResultMessage` `orchestrator_complete` emission on `state.run_complete_payload is None`

If the lead calls `RunComplete` *and* a `ResultMessage` arrives later in the same iteration, today's code would emit `orchestrator_complete` twice. Gate the legacy path.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_orchestrator_complete_emitted_exactly_once(monkeypatch):
    """A RunComplete tool call followed by a ResultMessage emits orchestrator_complete only once."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    captured: list[tuple] = []
    monkeypatch.setattr(
        "agent.station_orchestrator.post_webhook",
        lambda config, event, payload: captured.append((event, payload)),
    )

    tool_use = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    am = AssistantMessage(content=[tool_use], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(am, "session_id", "sess-1")
    handle_stream_event(am, config={}, run_id="run-x", log_file=None, state=state)

    rm = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50, is_error=False,
        num_turns=1, session_id="sess-1", total_cost_usd=0.0, usage=None,
        result="Final summary. All workers have completed.",
    )
    handle_stream_event(rm, config={}, run_id="run-x", log_file=None, state=state)

    oc_count = sum(1 for (e, _p) in captured if e == "orchestrator_complete")
    assert oc_count == 1, f"Expected exactly one orchestrator_complete; got {oc_count}"
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_orchestrator_complete_emitted_exactly_once -v
```

Expected:

```
FAILED ... AssertionError: Expected exactly one orchestrator_complete; got 2
```

(The first comes from Task 3's `RunComplete` branch; the second from the existing `ResultMessage` branch that hits `_is_work_complete("Final summary…")`.)

**Step 3: Implementation — gate the `ResultMessage` emission.**

In `agent/station_orchestrator.py`'s `elif isinstance(message, ResultMessage):` block (around line 1461), find the `post_webhook(config, "orchestrator_complete", ...)` call (around line 1529). Wrap it in a guard:

```python
        # Final flush of accumulated tokens
        if state:
            post_webhook(config, "progress_update", {
                "run_id": f"run-{run_id}",
                "tokens_input": state.tokens_in,
                "tokens_output": state.tokens_out,
                "tokens_total": state.tokens_in + state.tokens_out,
                "turns": state.turns,
            })
        # #385: if the lead already called the RunComplete tool, the
        # authoritative orchestrator_complete fired from the ToolUseBlock
        # branch. Skip the legacy emission to keep the contract single-firing.
        if state is not None and state.run_complete_payload is not None:
            logger.info(
                "Skipping legacy ResultMessage orchestrator_complete — "
                "RunComplete tool already latched the payload."
            )
            return
        post_webhook(config, "orchestrator_complete", {
            "run_id": f"run-{run_id}",
            "is_error": getattr(message, "is_error", False),
            "duration_ms": getattr(message, "duration_ms", 0),
            "num_turns": getattr(message, "num_turns", 0),
        })
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_orchestrator_complete_emitted_exactly_once -v
```

Expected:

```
PASSED ... test_orchestrator_complete_emitted_exactly_once
```

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "fix(orchestrator): gate legacy ResultMessage emission once RunComplete latched"
```

---

### Task 5 — Update `build_team_prompt` and `build_followup_prompt` with the contract paragraph

The lead must be told the tool exists and that calling it is the only way to end the run cleanly.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_team_prompt_includes_run_complete_contract():
    """build_team_prompt must mention RunComplete in its authoritative contract."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="owner/repo",
        issues=[{"number": 1, "title": "x", "labels": []}],
        config={"models": {}, "limits": {}},
        run_id="run-x",
        workspace="/tmp/ws",
        worktree_paths={},
        vision=None,
        project_mode="single",
        approved_plan_paths=[],
    )
    assert "RunComplete" in prompt, (
        "build_team_prompt must instruct the lead to call RunComplete (issue #385)"
    )
    # Status values must be documented in the prompt so the lead picks the right one.
    assert "success" in prompt and "partial" in prompt and "blocked" in prompt


def test_followup_prompt_includes_run_complete_contract():
    from agent.station_orchestrator import build_followup_prompt
    prompt = build_followup_prompt(workspace="/tmp/ws", operator_messages=[])
    assert "RunComplete" in prompt, (
        "build_followup_prompt must keep the RunComplete contract on every iteration"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_team_prompt_includes_run_complete_contract dashboard/backend/tests/test_run_complete_tool.py::test_followup_prompt_includes_run_complete_contract -v
```

Expected: 2 failures (current prompts don't mention `RunComplete`).

**Step 3: Implementation — extend both prompts.**

Define a module-level constant in `agent/station_orchestrator.py` (right above `build_team_prompt`):

```python
_RUN_COMPLETE_CONTRACT = """
## Ending the run

When all teammates are done — or you cannot proceed further — call the
`RunComplete` tool with a structured summary. This is the ONLY way to end
the run cleanly. Do not announce "the work is done" in prose; the
orchestrator does not read your prose for completion.

Status values:
- "success": all in-flight issues have a verdict.
- "partial": some issues progressed, some did not (record the rest in
  `verdicts` with `decision: "SKIP"` and a reason).
- "blocked": you cannot proceed without operator input.

Each `verdicts` entry must include `project`, `decision`
(APPROVE | APPROVE_INTEGRATION | PR | REJECT | SKIP), and may include
`issue_number`, `reasoning`, `branch`, and `base_branch`.
"""
```

In `build_team_prompt`, find the end of the assembled prompt (the `return prompt` line). Just before it, append:

```python
    prompt += _RUN_COMPLETE_CONTRACT
    return prompt
```

In `build_followup_prompt`, do the same at the end of the function:

```python
    prompt += _RUN_COMPLETE_CONTRACT
    return prompt
```

**Step 4: Run the tests — confirm they pass.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_team_prompt_includes_run_complete_contract dashboard/backend/tests/test_run_complete_tool.py::test_followup_prompt_includes_run_complete_contract -v
```

Expected: 2 passes.

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "feat(orchestrator): instruct the lead to call RunComplete in team and followup prompts"
```

---

### Task 6 — Wire the `RunComplete` MCP server into `ClaudeAgentOptions`

The tool has to be registered with the SDK or the lead can't call it.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_orchestrate_registers_run_complete_server(monkeypatch, tmp_path):
    """ClaudeAgentOptions.mcp_servers must include 'run_complete' and allowed_tools includes it."""
    from agent import station_orchestrator as so

    captured_options: list = []

    class _FakeClient:
        def __init__(self, *, options=None):
            captured_options.append(options)

        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def query(self, prompt): pass
        async def receive_response(self):
            # Yield a single SystemMessage(init) + immediately a RunComplete tool call.
            from unittest.mock import MagicMock
            init = MagicMock(spec=so.SystemMessage)
            init.subtype = "init"; init.session_id = "sess-1"
            yield init
        async def interrupt(self): pass

    monkeypatch.setattr(so, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)

    import asyncio
    asyncio.run(so.orchestrate(
        {"projects": [{"name": "owner/repo", "enabled": True}], "limits": {}, "models": {}},
        "20260514T120000Z", str(tmp_path),
    ))

    assert captured_options, "Expected ClaudeAgentOptions to be built and captured"
    opts = captured_options[0]
    assert "run_complete" in (opts.mcp_servers or {}), (
        "mcp_servers must include 'run_complete' (issue #385)"
    )
    allowed = opts.allowed_tools or []
    assert any("RunComplete" in t for t in allowed), (
        "allowed_tools must include mcp__run_complete__RunComplete"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_orchestrate_registers_run_complete_server -v
```

Expected:

```
FAILED ... AssertionError: mcp_servers must include 'run_complete' (issue #385)
```

**Step 3: Implementation — register the server.**

In `agent/station_orchestrator.py`, locate the `ClaudeAgentOptions(...)` build site introduced by #384 (just above the `async with ClaudeSDKClient(options=options)` block). Modify it:

```python
                from agent.tools.run_complete import build_run_complete_server
                _run_complete_server = build_run_complete_server()

                options = ClaudeAgentOptions(
                    cwd=workspace,
                    env={
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        "GITHUB_REPO": repo,
                    },
                    mcp_servers={
                        "run_complete": _run_complete_server,
                        "playwright": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@playwright/mcp@latest"],
                        },
                        "ref": {
                            "type": "http",
                            "url": "https://api.ref.tools/mcp",
                        },
                    },
                    allowed_tools=[
                        "Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent",
                        "mcp__playwright__*", "mcp__ref__*",
                        "mcp__run_complete__RunComplete",
                    ],
                    # ... rest unchanged ...
                )
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_orchestrate_registers_run_complete_server -v
```

Expected:

```
PASSED ... test_orchestrate_registers_run_complete_server
```

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "feat(orchestrator): register RunComplete MCP server in ClaudeAgentOptions"
```

---

### Task 7 — Inner-loop exit on `run_complete_payload`; fallback on `_is_work_complete`

After Tasks 3–6, the *webhook* fires authoritatively from the tool path. The inner orchestrate loop still terminates on `_is_work_complete(...)` from the #384 rewrite. Switch the primary exit to `state.run_complete_payload is not None` and keep `_is_work_complete` as a fallback for one release.

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_orchestrator_clientsdk.py` (the file created in #384):

```python
def test_inner_loop_exits_on_run_complete(monkeypatch, tmp_path):
    """The inner async-for exits when run_complete_payload latches."""
    from agent import station_orchestrator as so
    from unittest.mock import MagicMock

    _FakeClient.instances.clear() if hasattr(_FakeClient, "instances") else None

    init = MagicMock(spec=so.SystemMessage)
    init.subtype = "init"; init.session_id = "sess-1"

    # An AssistantMessage carrying a RunComplete ToolUseBlock.
    from claude_agent_sdk.types import ToolUseBlock, AssistantMessage
    tu = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "done"},
    )
    am = AssistantMessage(content=[tu], usage={}, model="claude-opus-4-7", parent_tool_use_id=None)
    setattr(am, "session_id", "sess-1")

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        # iter 1: init + tool-use. The loop should exit after the tool call;
        # if it tried to follow up, our script has no iter 2.
        c._scripted_messages = [[init, am]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)

    import asyncio
    asyncio.run(so.orchestrate(
        {"projects": [{"name": "owner/repo", "enabled": True}], "limits": {}, "models": {}},
        "20260514T120000Z", str(tmp_path),
    ))

    client = _FakeClient.instances[0]
    # If the loop re-entered, queries would be > 1. RunComplete should exit after 1.
    assert len(client.queries) == 1, (
        f"Inner loop should exit after RunComplete tool call; got {len(client.queries)} queries"
    )


def test_fallback_when_tool_not_called(monkeypatch, tmp_path, caplog):
    """If the lead never calls RunComplete, _is_work_complete is the fallback.

    For one release window after #385, prose-matched completion still
    terminates the run — with a warning so the operator can spot leads
    that haven't migrated to the tool contract.
    """
    from agent import station_orchestrator as so
    from unittest.mock import MagicMock

    _FakeClient.instances.clear() if hasattr(_FakeClient, "instances") else None

    rm = MagicMock(spec=so.ResultMessage)
    rm.session_id = "sess-1"
    rm.result = "Final summary. All workers have completed."
    rm.is_error = False; rm.duration_ms = 100; rm.num_turns = 1

    def _client_factory(options=None):
        c = _FakeClient(options=options)
        c._scripted_messages = [[rm]]
        return c

    monkeypatch.setattr(so, "ClaudeSDKClient", _client_factory)
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)

    import asyncio, logging
    caplog.set_level(logging.WARNING)
    asyncio.run(so.orchestrate(
        {"projects": [{"name": "owner/repo", "enabled": True}], "limits": {}, "models": {}},
        "20260514T120000Z", str(tmp_path),
    ))

    # Run completed — the loop exited via the fallback heuristic.
    # The fallback path must log a warning so operators can see "lead did not
    # call RunComplete" runs.
    fallback_logged = any(
        "RunComplete" in rec.message and "fallback" in rec.message.lower()
        for rec in caplog.records
    )
    assert fallback_logged, "Fallback path must log a warning about missing RunComplete tool call"
```

**Step 2: Run the tests — confirm they fail.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_inner_loop_exits_on_run_complete dashboard/backend/tests/test_orchestrator_clientsdk.py::test_fallback_when_tool_not_called -v
```

Expected: both fail — the loop currently exits only on `_is_work_complete`, and there's no fallback warning.

**Step 3: Implementation — update the inner-loop exit.**

In `agent/station_orchestrator.py`, find the inner `async for message in client.receive_response():` block (introduced by #384). Replace the `if isinstance(message, ResultMessage): ...` completion check with:

```python
                            # #385 primary completion gate: the lead called
                            # the RunComplete tool. handle_stream_event
                            # latched state.run_complete_payload; we exit
                            # the iterator naturally.
                            if state.run_complete_payload is not None:
                                work_complete = True
                                logger.info(
                                    "RunComplete tool received; breaking SDK stream"
                                )
                                break

                            # Fallback (one release window) — _is_work_complete
                            # prose match. Logged loudly so operators can
                            # spot runs whose lead hasn't migrated to the
                            # tool contract.
                            if isinstance(message, ResultMessage):
                                result_text = getattr(message, "result", "")
                                if _is_work_complete(result_text):
                                    logger.warning(
                                        "RunComplete fallback engaged: lead did "
                                        "not call the tool; relying on prose "
                                        "match. Run: run-%s",
                                        run_id,
                                    )
                                    work_complete = True
                                    break
```

(Note: the literal substring `"fallback"` and `"RunComplete"` together let the test's `caplog` search pass.)

**Step 4: Run the tests — confirm they pass.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py::test_inner_loop_exits_on_run_complete dashboard/backend/tests/test_orchestrator_clientsdk.py::test_fallback_when_tool_not_called -v
```

Expected: 2 passes.

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_clientsdk.py
$ git commit -m "feat(orchestrator): exit inner loop on RunComplete latch, fallback warns on prose match"
```

---

### Task 8 — Retry-after-malformed: the lead can call `RunComplete` twice; the second call wins

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_retry_after_malformed_run_complete(monkeypatch):
    """A malformed RunComplete followed by a valid one latches on the valid call."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    monkeypatch.setattr("agent.station_orchestrator.post_webhook", lambda *a, **k: None)

    bad = ToolUseBlock(id="tu-bad", name="RunComplete", input={"verdicts": []})
    am1 = AssistantMessage(content=[bad], usage={}, model="x", parent_tool_use_id=None)
    setattr(am1, "session_id", "sess-1")
    handle_stream_event(am1, config={}, run_id="r", log_file=None, state=state)
    assert state.run_complete_payload is None

    good = ToolUseBlock(
        id="tu-good", name="RunComplete",
        input={"status": "success", "verdicts": [], "summary": "retry worked"},
    )
    am2 = AssistantMessage(content=[good], usage={}, model="x", parent_tool_use_id=None)
    setattr(am2, "session_id", "sess-1")
    handle_stream_event(am2, config={}, run_id="r", log_file=None, state=state)
    assert state.run_complete_payload is not None
    assert state.run_complete_payload["summary"] == "retry worked"
```

**Step 2: Run the test — confirm it passes (Task 3's implementation already covers it).**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py::test_retry_after_malformed_run_complete -v
```

If it fails, check Task 3's implementation: the `if state.run_complete_payload is None:` guard is what allows the second call to latch. The malformed first call returns early in the `except ValidationError:` branch without touching state, so the second call's `state.run_complete_payload is None` check is True.

Expected:

```
PASSED ... test_retry_after_malformed_run_complete
```

**Step 3: Implementation — if failing, add a once-only latch guard.**

Confirm in the `RunComplete` branch (Task 3):

```python
                        else:
                            if state is not None and state.run_complete_payload is None:
                                state.run_complete_payload = parsed.model_dump()
                                post_webhook(...)
```

The `is None` check is the once-only latch.

**Step 4: Re-run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v
```

Expected: all tests in the file pass.

**Step 5: Commit.**

```
$ git add dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "test(run-complete): assert retry-after-malformed latches on the valid second call"
```

---

### Task 9 — Documentation and final suite-wide green check

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_run_complete_tool.py`:

```python
def test_is_work_complete_still_exists_as_fallback():
    """During the one-release fallback window, _is_work_complete remains importable.

    The function will be removed in the follow-up PR once the fallback rate
    hits zero in staging.
    """
    from agent.station_orchestrator import _is_work_complete
    # Behaviour is the same as before — sanity-check one branch.
    assert _is_work_complete("Final summary. All workers have completed.") is True
    assert _is_work_complete("just chatting") is False


def test_orchestrator_complete_payload_carries_structured_verdicts(monkeypatch):
    """The orchestrator_complete webhook payload has a `verdicts` list when RunComplete fires."""
    from agent.station_orchestrator import _StreamState, handle_stream_event
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    state = _StreamState(main_session_id="sess-1")
    captured: list[dict] = []
    monkeypatch.setattr(
        "agent.station_orchestrator.post_webhook",
        lambda config, event, payload: captured.append({"event": event, "payload": payload}),
    )

    tu = ToolUseBlock(
        id="tu-1", name="RunComplete",
        input={
            "status": "partial",
            "verdicts": [
                {"project": "owner/repo", "issue_number": 2, "decision": "SKIP",
                 "reasoning": "out of scope"},
            ],
            "summary": "one done, one skipped",
        },
    )
    am = AssistantMessage(content=[tu], usage={}, model="x", parent_tool_use_id=None)
    setattr(am, "session_id", "sess-1")
    handle_stream_event(am, config={}, run_id="r", log_file=None, state=state)

    oc = next(c for c in captured if c["event"] == "orchestrator_complete")
    assert oc["payload"]["status"] == "partial"
    assert len(oc["payload"]["verdicts"]) == 1
    assert oc["payload"]["verdicts"][0]["decision"] == "SKIP"
```

**Step 2: Run the tests — confirm they pass.**

```
$ python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v
```

Expected: full file passes (all tests across Tasks 1–9).

**Step 3: Documentation updates.**

In `docs/architecture.md` (or whichever doc owns the completion-signal description), find the section describing how a run ends. Replace prose like:

> The orchestrator detects completion by string-matching the lead's final message via `_is_work_complete()`.

with:

> The lead agent calls the in-process `RunComplete` SDK tool (registered as the `run_complete` MCP server) with a structured payload: `status` (success | partial | blocked), `verdicts[]`, and `summary`. The orchestrator's `handle_stream_event` observes the `ToolUseBlock`, validates the payload against `RunCompleteInput`, latches it onto `_StreamState.run_complete_payload`, and emits the authoritative `orchestrator_complete` webhook carrying the structured verdicts. The legacy `_is_work_complete` prose-matching path remains as a fallback for one release and logs a warning when engaged.

Also update `docs/configuration.md` if it lists allowed-tools — `mcp__run_complete__RunComplete` is now in the default set.

**Step 4: Run the full backend suite.**

```
$ python -m pytest dashboard/backend/tests/ -q
```

Expected: green.

```
$ grep -n "_is_work_complete" agent/station_orchestrator.py
```

Expected: still present (fallback path); two call sites — the inner-loop fallback and the existing `handle_stream_event` `ResultMessage` branch that also still uses it as a gate. That's intentional under the one-release window. A follow-up PR will delete `_is_work_complete` after staging confirms the fallback warning rate is zero.

**Step 5: Commit and open the PR.**

```
$ git add docs/ dashboard/backend/tests/test_run_complete_tool.py
$ git commit -m "docs+test: document RunComplete contract and pin final structured-verdicts shape"
```

```
$ gh pr create --base dev --title "feat(orchestrator): RunComplete SDK tool replaces prose completion (#385)" --body "Closes #385. Adds agent/tools/run_complete.py with a pydantic-validated RunComplete SDK tool. Wires it into ClaudeAgentOptions; handle_stream_event latches the payload and emits the single authoritative orchestrator_complete. Legacy _is_work_complete kept for one release as a fallback that logs a warning. Per memory, PRs target dev."
```

---

## Verification checklist

- [ ] `python -m pytest dashboard/backend/tests/test_run_complete_tool.py -v` → all tests pass.
- [ ] `python -m pytest dashboard/backend/tests/test_orchestrator_clientsdk.py -v` → all tests pass (including the two added in Task 7).
- [ ] `python -m pytest dashboard/backend/tests/ -q` → suite green.
- [ ] `grep -n "run_complete_payload" agent/station_orchestrator.py` → at least two matches (field declaration + latch site + inner-loop exit).
- [ ] `grep -n "RunComplete" agent/station_orchestrator.py` → matches in the prompts, `handle_stream_event`, and `allowed_tools`.
- [ ] `python -c "from agent.tools.run_complete import build_run_complete_server, run_complete_handler, RunCompleteInput; print('OK')"` → prints `OK`.
- [ ] One `orchestrator_complete` event per run in both paths (RunComplete-driven and fallback) — verified by `test_orchestrator_complete_emitted_exactly_once`.
