from datetime import datetime, timezone
from app.services.vision_render import render_vision_doc


def test_render_includes_all_seven_sections_in_order():
    doc = {
        "problem": "P", "users": "U", "end_state": "E",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    headers = [line for line in md.splitlines() if line.startswith("## ")]
    assert headers == [
        "## Problem", "## Users", "## End-state", "## Non-goals",
        "## Principles", "## Horizons", "## Anti-patterns",
    ]
    assert "P" in md and "U" in md and "Pr" in md
    assert md.startswith("# Vision — o/r\n")
    assert "*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*" in md


def test_render_handles_empty_section_with_placeholder():
    doc = {"problem": "P", "users": "", "end_state": "E", "non_goals": "",
           "principles": "", "horizons": "", "anti_patterns": ""}
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    assert "_(not specified)_" in md
