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
    # Splitter linkage (#391, gated on #385). Parent split-decision runs
    # list the sub-run IDs they spawned; sub-runs name their parent.
    # Both default to "empty" so single-issue runs — the common case —
    # need not populate them.
    sub_runs: list[str] = Field(
        default_factory=list,
        description="Sub-run IDs spawned from this run (parent runs only).",
    )
    parent_run: str | None = Field(
        default=None,
        description="Parent run ID if this is a sub-run.",
    )


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
