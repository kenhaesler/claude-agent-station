"""coordinator/decide split pre-dispatch hook (#391).

The hook is feature-gated by ``STATION_SPLIT_ENABLED=1``. With the flag
off the call is a no-op so PR-3 merges to ``dev`` without changing
production behaviour. Once an operator opts in, the hook calls the
``maybe_split`` heuristic; on a positive signal it dispatches the
splitter LLM agent (mocked here) and returns the decision payload for
the caller to act on.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_decide_falls_through_when_flag_disabled(monkeypatch):
    monkeypatch.delenv("STATION_SPLIT_ENABLED", raising=False)
    from agent.coordinator import decide
    issue = {"number": 27, "title": "x", "body": "y", "labels": []}
    with patch("agent.coordinator.decide.run_splitter", new=AsyncMock()) as splitter_mock:
        ok = await decide.maybe_run_splitter(issue, run_id="r1",
                                              repo_summary="", vision="")
    assert ok is None
    splitter_mock.assert_not_called()


@pytest.mark.asyncio
async def test_decide_skips_splitter_when_heuristic_says_no(monkeypatch):
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    issue = {"number": 27, "title": "x", "body": "small", "labels": []}
    with patch("agent.coordinator.decide.run_splitter", new=AsyncMock()) as splitter_mock:
        ok = await decide.maybe_run_splitter(issue, run_id="r1",
                                              repo_summary="", vision="")
    assert ok is None
    splitter_mock.assert_not_called()


@pytest.mark.asyncio
async def test_decide_invokes_splitter_when_eligible(monkeypatch):
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from agent.issue_splitter.schema import SplitDecision, SubIssueProposal
    decision = SplitDecision(
        proposals=(
            SubIssueProposal("a", "b", (), ("x",)),
            SubIssueProposal("c", "d", (), ("y",)),
        ),
    )
    issue = {"number": 27, "title": "x",
             "body": "## Acceptance criteria\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
             "labels": []}
    with patch("agent.coordinator.decide.run_splitter",
               new=AsyncMock(return_value=decision)) as splitter_mock:
        result = await decide.maybe_run_splitter(issue, run_id="r1",
                                                  repo_summary="", vision="")
    splitter_mock.assert_called_once()
    assert result is decision


@pytest.mark.asyncio
async def test_decide_collapses_empty_proposals_to_none(monkeypatch):
    """The splitter's empty-array output ("don't split") parses as a
    SplitDecision with proposals=(). The hook MUST collapse that to
    ``None`` so the caller has a single signal to branch on; without
    this collapse, execute_split_decision would happily label the
    parent ``split`` and create an empty integration branch.

    Regression guard for PR #423 review.
    """
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from agent.issue_splitter.schema import SplitDecision

    empty_decision = SplitDecision(proposals=(), warnings=())
    issue = {"number": 27, "title": "x",
             "body": "## Acceptance criteria\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
             "labels": []}
    with patch("agent.coordinator.decide.run_splitter",
               new=AsyncMock(return_value=empty_decision)):
        result = await decide.maybe_run_splitter(
            issue, run_id="r1", repo_summary="", vision="",
        )
    assert result is None


@pytest.mark.asyncio
async def test_decide_threads_cwd_to_run_splitter(monkeypatch):
    """``cwd`` must reach the SDK runner so the splitter inspects the
    project tree rather than the launcher's ``/app`` (the very fix PR-2
    landed). Regression guard for PR #423 review.
    """
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from agent.issue_splitter.schema import SplitDecision, SubIssueProposal

    decision = SplitDecision(proposals=(
        SubIssueProposal("a", "b", (), ("x",)),
        SubIssueProposal("c", "d", (), ("y",)),
    ))
    issue = {
        "number": 27, "title": "x",
        "body": "## Acceptance criteria\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
        "labels": [],
    }
    with patch(
        "agent.coordinator.decide.run_splitter",
        new=AsyncMock(return_value=decision),
    ) as splitter_mock:
        await decide.maybe_run_splitter(
            issue, run_id="r1", repo_summary="", vision="",
            cwd="/workspaces/x-y",
        )
    splitter_mock.assert_called_once()
    assert splitter_mock.call_args.kwargs["cwd"] == "/workspaces/x-y"
