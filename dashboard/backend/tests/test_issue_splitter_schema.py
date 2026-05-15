"""Splitter JSON output parser (#391)."""
from __future__ import annotations

import json

import pytest

from agent.issue_splitter.schema import (
    SplitDecision,
    SplitterError,
    SubIssueProposal,
    parse_splitter_output,
)


def test_parse_minimum_valid_two_items():
    raw = json.dumps([
        {"title": "Add login endpoint",
         "body": "POST /api/auth/login returns 200 + token.",
         "labels": ["backend"],
         "acceptance": ["Returns 200", "Issues JWT"],
         "depends_on": None},
        {"title": "Add /me endpoint",
         "body": "GET /api/me returns the current user from the token.",
         "labels": ["backend"],
         "acceptance": ["Returns 200 when authenticated"],
         "depends_on": 0},
    ])
    decision = parse_splitter_output(raw)
    assert isinstance(decision, SplitDecision)
    assert len(decision.proposals) == 2
    assert decision.proposals[0].title == "Add login endpoint"
    assert decision.proposals[1].depends_on == 0


def test_parse_rejects_single_item():
    raw = json.dumps([{"title": "x", "body": "y", "labels": [], "acceptance": ["a"]}])
    with pytest.raises(SplitterError, match="at least 2"):
        parse_splitter_output(raw)


def test_parse_rejects_more_than_five():
    """Refuse rather than truncate: silently dropping items would break
    depends_on index math and lose work. The prompt's 2-5 cap is the
    contract — violations are the splitter's bug, not ours to paper over.
    """
    items = [
        {"title": f"item {i}", "body": "b", "labels": [], "acceptance": ["a"], "depends_on": None}
        for i in range(7)
    ]
    with pytest.raises(SplitterError, match="at most 5"):
        parse_splitter_output(json.dumps(items))


def test_parse_rejects_string_where_labels_list_expected():
    """A bare string for ``labels`` (Sonnet's known quirk for single-label
    issues) would otherwise be iterated character-by-character and produce
    garbage labels like ('b','a','c','k','e','n','d'). Refuse explicitly.
    Regression guard for PR #421 review finding."""
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": "backend",
         "acceptance": ["x"], "depends_on": None},
        {"title": "c", "body": "d", "labels": ["backend"],
         "acceptance": ["y"], "depends_on": None},
    ])
    with pytest.raises(SplitterError, match="labels"):
        parse_splitter_output(raw)


def test_parse_rejects_string_where_acceptance_list_expected():
    """Same defence for ``acceptance``."""
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [],
         "acceptance": "must work", "depends_on": None},
        {"title": "c", "body": "d", "labels": [],
         "acceptance": ["x"], "depends_on": None},
    ])
    with pytest.raises(SplitterError, match="acceptance"):
        parse_splitter_output(raw)


def test_parse_rejects_malformed_json():
    with pytest.raises(SplitterError, match="json"):
        parse_splitter_output("not json")


def test_parse_rejects_missing_required_fields():
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"]},
        {"title": "c"},  # missing body, labels, acceptance
    ])
    with pytest.raises(SplitterError, match="missing"):
        parse_splitter_output(raw)


def test_parse_rejects_invalid_depends_on():
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"], "depends_on": None},
        {"title": "c", "body": "d", "labels": [], "acceptance": ["x"], "depends_on": 99},
    ])
    with pytest.raises(SplitterError, match="depends_on"):
        parse_splitter_output(raw)


def test_parse_accepts_fenced_json_block():
    """The SDK splitter sometimes wraps its output in ```json fences."""
    items = [
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"], "depends_on": None},
        {"title": "c", "body": "d", "labels": [], "acceptance": ["y"], "depends_on": None},
    ]
    raw = "```json\n" + json.dumps(items) + "\n```"
    decision = parse_splitter_output(raw)
    assert len(decision.proposals) == 2


def test_parse_empty_array_is_run_as_is():
    """An empty array means the splitter chose not to split — valid."""
    decision = parse_splitter_output("[]")
    assert decision.proposals == ()
    assert decision.warnings == ()


def test_parse_rejects_self_dependency():
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"], "depends_on": None},
        {"title": "c", "body": "d", "labels": [], "acceptance": ["y"], "depends_on": 1},
    ])
    with pytest.raises(SplitterError, match="depends_on"):
        parse_splitter_output(raw)
