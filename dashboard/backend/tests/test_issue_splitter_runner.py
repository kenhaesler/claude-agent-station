"""run_splitter spawns the SDK session and parses its output (#391).

These tests mock ``_invoke_splitter_sdk`` so the real Claude Agent SDK
is never touched — the SDK roundtrip is exercised by the integration
test that lands with PR-4. Here we verify only the parse/return contract
and error propagation.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.issue_splitter.runner import run_splitter
from agent.issue_splitter.schema import SplitDecision, SplitterError


@pytest.mark.asyncio
async def test_run_splitter_returns_parsed_decision():
    captured = json.dumps([
        {"title": "A", "body": "B", "labels": ["x"], "acceptance": ["a1"], "depends_on": None},
        {"title": "B", "body": "C", "labels": ["x"], "acceptance": ["a1"], "depends_on": 0},
    ])
    with patch(
        "agent.issue_splitter.runner._invoke_splitter_sdk",
        new=AsyncMock(return_value=captured),
    ):
        decision = await run_splitter(
            issue={"number": 27, "body": "x", "labels": []},
            run_id="run-split-decision-1",
            repo_summary="repo info",
            vision="vision text",
        )
    assert isinstance(decision, SplitDecision)
    assert len(decision.proposals) == 2
    assert decision.proposals[1].depends_on == 0


@pytest.mark.asyncio
async def test_run_splitter_propagates_schema_errors():
    with patch(
        "agent.issue_splitter.runner._invoke_splitter_sdk",
        new=AsyncMock(return_value="garbage"),
    ):
        with pytest.raises(SplitterError):
            await run_splitter(
                issue={"number": 1, "body": "x", "labels": []},
                run_id="r1",
                repo_summary="",
                vision="",
            )


@pytest.mark.asyncio
async def test_run_splitter_empty_array_means_run_as_is():
    """Empty array -> SplitDecision with no proposals (controller's signal
    to run the parent as-is). We always return SplitDecision rather than
    ``None`` so callers don't have to disambiguate "splitter said no" from
    "splitter errored" via the type system."""
    with patch(
        "agent.issue_splitter.runner._invoke_splitter_sdk",
        new=AsyncMock(return_value="[]"),
    ):
        decision = await run_splitter(
            issue={"number": 1, "body": "x", "labels": []},
            run_id="r1",
            repo_summary="",
            vision="",
        )
    assert isinstance(decision, SplitDecision)
    assert decision.proposals == ()
