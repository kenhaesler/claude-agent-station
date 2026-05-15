"""GitHub issue creation for splitter (#391).

Tests verify the wiring against an injected ``gh`` client. The real ``gh``
adapter lives in PR-3 (the splitter controller); these tests mock the
methods this module calls so the unit boundary stays at the protocol seam.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.issue_splitter.github_ops import (
    SPLITTER_LABEL,
    add_backlink_comment,
    create_sub_issues,
    ensure_splitter_label,
)
from agent.issue_splitter.schema import SubIssueProposal


def _proposal(title: str, depends_on: int | None = None) -> SubIssueProposal:
    return SubIssueProposal(
        title=title,
        body="parent-context inlined here\n\nimplementation detail",
        labels=("backend",),
        acceptance=("Returns 200",),
        depends_on=depends_on,
    )


def test_ensure_splitter_label_creates_when_missing():
    gh = MagicMock()
    gh.label_exists.return_value = False
    ensure_splitter_label("kenhaesler", "claude-agent-station", gh)
    gh.create_label.assert_called_once()
    args, _ = gh.create_label.call_args
    assert args[0] == "kenhaesler"
    assert args[1] == "claude-agent-station"
    assert args[2] == "splitter-proposed"


def test_ensure_splitter_label_idempotent_when_present():
    gh = MagicMock()
    gh.label_exists.return_value = True
    ensure_splitter_label("kenhaesler", "claude-agent-station", gh)
    gh.create_label.assert_not_called()


def test_create_sub_issues_posts_each_with_correct_labels():
    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [
        {"number": 101}, {"number": 102}, {"number": 103},
    ]
    parent = {
        "number": 27,
        "labels": ["backend", "auth"],
        "repo": "kenhaesler/claude-agent-station",
    }
    proposals = [_proposal("a"), _proposal("b", depends_on=0), _proposal("c")]
    created = create_sub_issues(parent, proposals, gh)

    assert [c["number"] for c in created] == [101, 102, 103]
    for call in gh.create_issue.call_args_list:
        kwargs = call.kwargs
        assert SPLITTER_LABEL in kwargs["labels"]
    # Body includes parent back-link.
    body_a = gh.create_issue.call_args_list[0].kwargs["body"]
    assert "Parent: #27" in body_a
    # depends_on of item 1 references sibling at index 0 -> #101.
    body_b = gh.create_issue.call_args_list[1].kwargs["body"]
    assert "Depends on #101" in body_b


def test_create_sub_issues_applies_parent_label_set():
    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]
    parent = {"number": 27, "labels": ["backend"], "repo": "x/y"}
    create_sub_issues(parent, [_proposal("a"), _proposal("b")], gh)
    labels = gh.create_issue.call_args_list[0].kwargs["labels"]
    assert "backend" in labels


def test_add_backlink_comment_writes_summary():
    gh = MagicMock()
    add_backlink_comment(
        parent_repo="x/y",
        parent_number=27,
        sub_numbers=[101, 102, 103],
        gh=gh,
    )
    gh.create_issue_comment.assert_called_once()
    body = gh.create_issue_comment.call_args.kwargs.get("body", "")
    assert "#101" in body and "#102" in body and "#103" in body


def test_create_sub_issues_rejects_forward_reference():
    """Schema validates depends_on bounds + self-ref but allows forward
    references (proposal 0 depending on proposal 2). create_sub_issues
    creates issues sequentially and populates the sibling-number map as
    it goes, so a forward reference would KeyError mid-loop. Refuse
    explicitly with a clear message. Regression guard for PR #422.
    """
    from agent.issue_splitter.schema import SplitterError

    gh = MagicMock()
    gh.label_exists.return_value = True
    # Bypass parser; construct proposals directly so we can plant a
    # forward reference the parser doesn't currently reject.
    proposals = [
        SubIssueProposal(
            title="first", body="b", labels=(), acceptance=("x",),
            depends_on=1,  # forward ref!
        ),
        SubIssueProposal(
            title="second", body="b", labels=(), acceptance=("x",),
            depends_on=None,
        ),
    ]
    parent = {"number": 27, "labels": [], "repo": "x/y"}
    with pytest.raises(SplitterError, match="forward reference"):
        create_sub_issues(parent, proposals, gh)
    # Nothing should have been created.
    gh.create_issue.assert_not_called()
