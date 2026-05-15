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


# ---------------------------------------------------------------------------
# _extract_assistant_text — regression guard for PR #422 finding.
#
# SDK assistant messages expose `content` as a list of content blocks
# (TextBlock / ToolUseBlock / ToolResultBlock dataclasses, or dict shapes
# in some SDK versions). `str(content)` would produce a Python repr like
# `[TextBlock(text='[...]')]` instead of the actual JSON payload, and
# `parse_splitter_output` would silently raise SplitterError downstream.
# These tests lock in the block-walking extraction at the seam.
# ---------------------------------------------------------------------------


from dataclasses import dataclass

from agent.issue_splitter.runner import _extract_assistant_text


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    name: str
    type: str = "tool_use"


def test_extract_text_from_block_list_dataclasses():
    """Realistic SDK shape: content is a list of TextBlock dataclasses."""
    content = [_TextBlock(text='[{"title": "a"}]')]
    assert _extract_assistant_text(content) == '[{"title": "a"}]'


def test_extract_text_skips_non_text_blocks():
    """A tool_use block must not contaminate the captured text."""
    content = [
        _ToolUseBlock(name="Read"),
        _TextBlock(text='[{"title": "a"}]'),
    ]
    assert _extract_assistant_text(content) == '[{"title": "a"}]'


def test_extract_text_from_dict_block_shape():
    """Some SDK versions hand back dict-shaped blocks instead of dataclasses."""
    content = [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    assert _extract_assistant_text(content) == "first\nsecond"


def test_extract_text_returns_none_when_no_text_blocks():
    content = [_ToolUseBlock(name="Read"), _ToolUseBlock(name="Grep")]
    assert _extract_assistant_text(content) is None


def test_extract_text_handles_bare_string_content():
    """Older SDK shapes occasionally hand back a single string."""
    # We wrap in a single-element list internally; the string itself
    # isn't a typed block, so we return None (no text-type marker).
    # The function's contract is "extract from text-typed blocks";
    # bare strings are out of contract.
    assert _extract_assistant_text("just a string") is None


def test_extract_text_returns_none_for_none_content():
    assert _extract_assistant_text(None) is None
