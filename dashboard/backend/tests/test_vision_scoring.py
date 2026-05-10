import json
import pytest
from unittest.mock import patch, MagicMock
from agent.vision_scoring import score_issues_against_vision

VISION = {
    "problem": "Self-host a Claude agent.",
    "users": "Solo devs.",
    "end_state": "A daily autonomous agent.",
    # Issue #335: VISION dicts in tests must include the new fields so the
    # template's `**vision` spread finds tech_stack and runtime_target.
    "tech_stack": "Python/FastAPI + Svelte 5.",
    "runtime_target": "Linux host (systemd).",
    "non_goals": "Multi-tenant.",
    "principles": "Solo simplicity.",
    "horizons": "Near-term: stability.",
    "anti_patterns": "Enterprise complexity.",
}


def test_score_issues_returns_input_with_added_fields():
    issues = [
        {"number": 1, "title": "Add daily run", "body": ""},
        {"number": 2, "title": "Add SSO", "body": ""},
    ]
    fake_response = json.dumps([
        {"number": 1, "score": 0.9, "why": "advances daily autonomy"},
        {"number": 2, "score": 0.1, "why": "violates non-goal"},
    ])

    with patch("agent.vision_scoring._call_model", return_value=fake_response):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")

    by_num = {i["number"]: i for i in scored}
    assert by_num[1]["vision_score"] == 0.9
    assert by_num[2]["vision_score"] == 0.1
    assert "advances daily autonomy" in by_num[1]["vision_reason"]


def test_score_issues_falls_back_to_neutral_on_model_error():
    issues = [{"number": 1, "title": "x", "body": ""}]
    with patch("agent.vision_scoring._call_model", side_effect=RuntimeError("nope")):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")
    assert scored[0]["vision_score"] == 0.5
    assert scored[0]["vision_reason"] == ""


def test_score_issues_falls_back_on_malformed_json():
    issues = [{"number": 1, "title": "x", "body": ""}]
    with patch("agent.vision_scoring._call_model", return_value="not json"):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")
    assert scored[0]["vision_score"] == 0.5
