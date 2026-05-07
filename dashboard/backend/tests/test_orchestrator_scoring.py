"""Test that the orchestrator's combined ranking honours vision scores."""

import pytest
from unittest.mock import patch
from agent.station_orchestrator import _combined_rank_issues


VISION = {"problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
          "principles": "Pr", "horizons": "H", "anti_patterns": "A"}


def test_combined_rank_no_vision_preserves_priority_order():
    issues = [
        {"number": 1, "title": "x", "body": "", "labels": [{"name": "priority/low"}]},
        {"number": 2, "title": "x", "body": "", "labels": [{"name": "priority/critical"}]},
    ]
    out = _combined_rank_issues(issues, vision=None, weight=0.4, model="m")
    assert out[0]["number"] == 2  # critical first


def test_combined_rank_vision_can_promote_aligned_issue():
    issues = [
        {"number": 1, "title": "x", "body": "", "labels": []},  # unlabeled
        {"number": 2, "title": "x", "body": "", "labels": [{"name": "priority/high"}]},
    ]
    fake_scored = [
        {**issues[0], "vision_score": 0.95, "vision_reason": "very aligned"},
        {**issues[1], "vision_score": 0.20, "vision_reason": "off-mission"},
    ]
    with patch("agent.station_orchestrator.score_issues_against_vision",
               return_value=fake_scored):
        out = _combined_rank_issues(issues, vision=VISION, weight=0.6, model="m")
    # vision-strong (0.95) should win over priority-strong (high) when w=0.6
    assert out[0]["number"] == 1
