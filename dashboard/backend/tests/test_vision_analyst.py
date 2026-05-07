import json
import pytest
from unittest.mock import patch, MagicMock
from agent.vision_analyst import propose_gaps, format_proposal_body


VISION = {"problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
          "principles": "Pr", "horizons": "H", "anti_patterns": "A"}


def test_propose_gaps_returns_parsed_proposals():
    fake = json.dumps([
        {"title": "Add daily digest", "body": "Send a daily summary email", "labels": ["feature"], "priority": "medium"},
        {"title": "Cron resilience", "body": "Retry failed cron runs", "labels": ["enhancement"], "priority": "high"},
    ])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=fake):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) == 2
    assert proposals[0]["title"] == "Add daily digest"


def test_propose_gaps_caps_at_5():
    huge = json.dumps([{"title": f"x{i}", "body": "", "labels": [], "priority": "low"} for i in range(20)])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=huge):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) <= 5


def test_format_proposal_body_includes_disclaimer():
    body = format_proposal_body("The feature explanation.")
    assert "Proposed by Claude Station" in body
    assert "vision-suggested" in body
    assert "The feature explanation." in body
