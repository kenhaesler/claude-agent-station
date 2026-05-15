"""execute_split_decision side effects (#391).

Verifies the full splitter execution pipeline: create sub-issues, post
the backlink comment on the parent, label the parent ``split`` so it
won't be re-considered, and ensure the integration branch exists.

The GitHub client is replaced with a ``MagicMock`` via the
``_gh_client`` factory seam, and ``_ensure_integration_branch`` is
patched separately because it's a top-level helper with its own
network calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.coordinator.decide import execute_split_decision
from agent.issue_splitter.schema import SplitDecision, SubIssueProposal


@pytest.mark.asyncio
async def test_execute_split_decision_creates_sub_issues_and_backlinks():
    decision = SplitDecision(
        proposals=(
            SubIssueProposal("A", "body-a", (), ("x",)),
            SubIssueProposal("B", "body-b", (), ("y",), depends_on=0),
        ),
    )
    parent = {"number": 27, "title": "auth", "labels": ["backend"],
              "repo": "kenhaesler/claude-agent-station"}

    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]
    with patch("agent.coordinator.decide._gh_client", return_value=gh), \
         patch("agent.coordinator.decide._ensure_integration_branch") as iib:
        await execute_split_decision(parent, decision, run_id="rsd-1")

    assert gh.create_issue.call_count == 2
    gh.create_issue_comment.assert_called_once()
    iib.assert_called_once_with("kenhaesler/claude-agent-station", 27)
    # parent labelled "split" so the dispatcher won't re-pick it
    gh.add_labels.assert_called_once_with(
        "kenhaesler", "claude-agent-station", 27, ["split"],
    )


@pytest.mark.asyncio
async def test_execute_split_decision_tolerates_db_failure():
    """The DB persistence write is best-effort — a failure (e.g. legacy
    run row missing, brief unavailability) must NOT prevent the GitHub
    side effects from being considered successful. The helper swallows
    its own exceptions and logs at WARNING; this test guards against
    a regression that would surface a raw exception to the caller.
    """
    decision = SplitDecision(
        proposals=(SubIssueProposal("A", "b", (), ("x",)),),
    )
    parent = {"number": 27, "title": "x", "labels": [],
              "repo": "kenhaesler/claude-agent-station"}
    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.return_value = {"number": 101}
    # No real DB is set up in this test; the helper hits an "async_session
    # not configured" / "no such table" path and swallows it.
    with patch("agent.coordinator.decide._gh_client", return_value=gh), \
         patch("agent.coordinator.decide._ensure_integration_branch"):
        await execute_split_decision(parent, decision, run_id="rsd-2")
    gh.create_issue.assert_called_once()
