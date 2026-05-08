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


async def test_run_for_project_posts_started_and_finished_webhooks(monkeypatch, tmp_path):
    """run_for_project must POST started + finished events with mode=vision-bootstrap."""
    from agent import vision_analyst as va

    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append({"url": url, "json": json})
        class R:
            status_code = 200
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(va.httpx, "post", fake_post, raising=False)
    monkeypatch.setattr(va, "_ensure_workspace", lambda w, r: True)
    monkeypatch.setattr(va, "load_vision", lambda w: {
        "problem": "p", "users": "u", "end_state": "e",
        "non_goals": "n", "principles": "pr", "horizons": "h",
        "anti_patterns": "a",
    })
    monkeypatch.setattr(va, "propose_gaps", lambda w, v, r, m: [
        {"title": "T1", "body": "B1", "priority": "low"},
    ])
    monkeypatch.setattr(va, "create_proposed_issues", lambda r, p: [(101, p[0])])
    monkeypatch.setenv("STATION_WEBHOOK_URL", "http://test/api/webhook/run-event")
    monkeypatch.setenv("STATION_WORKSPACES", str(tmp_path))

    # Project with id=1
    from app.database import async_session, init_db
    from app.models import Project
    await init_db()
    async with async_session() as db:
        db.add(Project(id=1, repo="x/y", branch="main"))
        await db.commit()

    result = await va.run_for_project(1)
    assert result["ok"] is True

    assert len(posted) == 2
    started = posted[0]["json"]
    finished = posted[1]["json"]
    assert started["event"] == "started"
    assert started["mode"] == "vision-bootstrap"
    assert started["run_id"].startswith("run-vb-")
    assert finished["event"] == "finished"
    assert finished["mode"] == "vision-bootstrap"
    assert finished["status"] == "success"
    assert finished["vision_bootstrap_count"] == 1
    assert finished["vision_bootstrap_proposals"][0]["number"] == 101


def test_create_proposed_issues_pairs_failures_correctly(monkeypatch):
    """When proposal A fails and B succeeds, the (number, proposal) tuple for B
    correctly carries B's title — not A's."""
    from agent import vision_analyst as va

    proposals = [
        {"title": "Proposal A (will fail)", "body": "x", "priority": "low"},
        {"title": "Proposal B (will succeed)", "body": "y", "priority": "low"},
        {"title": "Proposal C (will succeed)", "body": "z", "priority": "low"},
    ]

    call_count = [0]

    def fake_run(cmd, *a, **kw):
        call_count[0] += 1
        # First call (Proposal A) fails
        if call_count[0] == 1:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "gh boom"})()
        # Subsequent calls succeed; URL ends with the issue number
        n = 100 + call_count[0]
        return type("R", (), {
            "returncode": 0,
            "stdout": f"https://github.com/x/y/issues/{n}\n",
            "stderr": "",
        })()

    monkeypatch.setattr("subprocess.run", fake_run)

    pairs = va.create_proposed_issues("x/y", proposals)
    assert len(pairs) == 2
    # First pair: issue 102 should be Proposal B (NOT A)
    assert pairs[0][0] == 102
    assert pairs[0][1]["title"] == "Proposal B (will succeed)"
    # Second pair: issue 103 should be Proposal C
    assert pairs[1][0] == 103
    assert pairs[1][1]["title"] == "Proposal C (will succeed)"
