"""Tests for agent.manager_review removal (#390).

The `manager_review.py` module (claude -p subprocess) was deleted in #390.
The manager is now a sibling agent inside the lead's SDK session.
See agent/agents/manager.md and the _read_verdicts_file helper in
agent/station_orchestrator.py.
"""
from __future__ import annotations

from pathlib import Path


def test_manager_review_module_is_deleted():
    """#390: the claude -p subprocess module must be gone."""
    repo = Path(__file__).resolve().parents[3]
    assert not (repo / "agent" / "manager_review.py").exists(), (
        "agent/manager_review.py must be deleted — the manager is now a "
        "sibling agent inside the lead's SDK session (#390)"
    )


def test_project_loop_does_not_import_manager_review():
    """project_loop.py must no longer import run_manager_review."""
    import inspect
    from agent import project_loop as pl

    src = inspect.getsource(pl)
    assert "run_manager_review" not in src, (
        "project_loop still references run_manager_review; "
        "the manager is now a sibling agent (#390)"
    )
    assert "manager_review" not in src, (
        "project_loop still imports from manager_review; "
        "the module was deleted in #390"
    )
