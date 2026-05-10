from datetime import datetime, timezone
from app.services.vision_render import render_vision_doc


def test_render_includes_all_nine_sections_in_order():
    doc = {
        "problem": "P", "users": "U", "end_state": "E",
        "tech_stack": "TS", "runtime_target": "RT",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    headers = [line for line in md.splitlines() if line.startswith("## ")]
    assert headers == [
        "## Problem", "## Users", "## End-state",
        "## Tech Stack", "## Runtime Target",
        "## Non-goals", "## Principles", "## Horizons", "## Anti-patterns",
    ]
    assert "P" in md and "U" in md and "Pr" in md and "TS" in md and "RT" in md
    assert md.startswith("# Vision — o/r\n")
    assert "*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*" in md


def test_render_handles_empty_section_with_placeholder():
    """Empty new fields use the same `_(not specified)_` placeholder
    as the original seven (issue #335 backward-compat)."""
    doc = {
        "problem": "P", "users": "", "end_state": "E",
        "tech_stack": "", "runtime_target": "",
        "non_goals": "", "principles": "",
        "horizons": "", "anti_patterns": "",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    assert "_(not specified)_" in md
    # All nine headings always present, regardless of body.
    assert "## Tech Stack" in md
    assert "## Runtime Target" in md


def test_vision_doc_optional_fields():
    """VisionDoc accepts payloads missing tech_stack / runtime_target — they
    default to empty string. Locks the back-compat behaviour for chats that
    were authored before issue #335 added the two fields.
    """
    from app.schemas import VisionDoc

    payload = {
        "problem": "P", "users": "U", "end_state": "E",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    doc = VisionDoc.model_validate(payload)
    assert doc.tech_stack == ""
    assert doc.runtime_target == ""
    dumped = doc.model_dump()
    assert dumped["tech_stack"] == ""
    assert dumped["runtime_target"] == ""
