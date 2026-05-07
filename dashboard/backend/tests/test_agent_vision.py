import os
import tempfile
from agent.vision import load_vision

SAMPLE = """\
# Vision — owner/repo

*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*

## Problem
The pain.

## Users
The audience.

## End-state
Done looks like this.

## Non-goals
Out of scope.

## Principles
How to choose.

## Horizons
Near, mid, long.

## Anti-patterns
Bad shapes.
"""


def test_load_vision_parses_all_seven_sections(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text(SAMPLE)
    v = load_vision(str(workspace))
    assert v is not None
    assert v["problem"].strip() == "The pain."
    assert v["non_goals"].strip() == "Out of scope."
    assert v["anti_patterns"].strip() == "Bad shapes."


def test_load_vision_returns_none_when_missing(tmp_path):
    assert load_vision(str(tmp_path)) is None


def test_load_vision_tolerates_partial_sections(tmp_path):
    """If some H2s are missing, return what we got and log a warning."""
    partial = "# Vision — o/r\n\n## Problem\nP\n\n## Users\nU\n"
    workspace = tmp_path / "ws"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text(partial)
    v = load_vision(str(workspace))
    assert v is not None
    assert v["problem"] == "P"
    assert v["users"] == "U"
    # Missing sections present as empty strings (not absent keys)
    assert v["end_state"] == ""
