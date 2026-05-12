"""Tests for the project_id fallback in handle_empty_backlog.

The orchestrator's config doesn't carry the dashboard's DB Project.id
(sync_db_to_config never wrote it). Without a fallback the vision
analyst could never dispatch on operator-edited configs and the
empty-backlog skip reason was a misleading 'no-vision'.

See diagnosis of run-20260512T112429Z (#TBD).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make sure `agent` is importable in tests
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a minimal projects table with one row, point STATION_DB_PATH at it."""
    db_path = tmp_path / "station.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY, repo TEXT NOT NULL UNIQUE)"
    )
    conn.execute("INSERT INTO projects (id, repo) VALUES (42, 'owner/proj-found')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("STATION_DB_PATH", str(db_path))
    yield db_path


def test_resolve_project_id_finds_existing(temp_db):
    from agent.station_orchestrator import _resolve_project_id_by_repo
    assert _resolve_project_id_by_repo("owner/proj-found") == 42


def test_resolve_project_id_returns_none_when_missing(temp_db):
    from agent.station_orchestrator import _resolve_project_id_by_repo
    assert _resolve_project_id_by_repo("owner/never-existed") is None


def test_resolve_project_id_returns_none_on_db_error(tmp_path, monkeypatch):
    from agent.station_orchestrator import _resolve_project_id_by_repo
    # Point at a path that doesn't exist; sqlite3 will create an empty
    # DB which has no projects table, raising OperationalError.
    monkeypatch.setenv("STATION_DB_PATH", str(tmp_path / "missing.db"))
    assert _resolve_project_id_by_repo("any/repo") is None


def test_handle_empty_backlog_dispatches_via_fallback(temp_db, tmp_path, monkeypatch):
    """When project_id arg is None but the DB has the project, the
    fallback resolves it and the analyst dispatch runs (skip_reason
    reflects dispatch, NOT the misleading 'no-vision')."""
    from agent.station_orchestrator import handle_empty_backlog

    # Create a fake workspace with docs/vision.md
    workspace = tmp_path / "ws"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text("# Vision\n")

    # Mock the network/process side-effects:
    #   has_open_vision_proposals → False  (no pending proposals)
    #   dispatch_vision_bootstrap → "dispatched"
    #   post_webhook → no-op
    with patch("agent.station_orchestrator.has_open_vision_proposals", return_value=False), \
         patch("agent.station_orchestrator.dispatch_vision_bootstrap", return_value="dispatched") as mock_dispatch, \
         patch("agent.station_orchestrator.post_webhook", return_value=None):
        skip = handle_empty_backlog(
            config={},
            repo="owner/proj-found",
            project_id=None,  # ← the bug condition we're fixing
            workspace=str(workspace),
            run_id="test-1",
        )
    assert skip == "no-eligible-issues-bootstrap-dispatched"
    mock_dispatch.assert_called_once_with(42)


def test_handle_empty_backlog_distinct_skip_when_no_db_row(temp_db, tmp_path):
    """If the repo has vision.md but the dashboard's DB has no row, the
    skip reason is the new distinct one — NOT the misleading 'no-vision'."""
    from agent.station_orchestrator import handle_empty_backlog

    workspace = tmp_path / "ws-no-row"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text("# Vision\n")

    with patch("agent.station_orchestrator.has_open_vision_proposals", return_value=False), \
         patch("agent.station_orchestrator.post_webhook", return_value=None):
        skip = handle_empty_backlog(
            config={},
            repo="owner/not-in-db",  # NOT in the DB
            project_id=None,
            workspace=str(workspace),
            run_id="test-2",
        )
    assert skip == "no-eligible-issues-vision-but-no-project-id"


def test_handle_empty_backlog_no_vision_still_says_no_vision(tmp_path):
    """The actual no-vision case must STILL produce 'no-eligible-issues-no-vision'."""
    from agent.station_orchestrator import handle_empty_backlog

    workspace = tmp_path / "ws-no-vision"
    workspace.mkdir()
    # No docs/vision.md

    with patch("agent.station_orchestrator.post_webhook", return_value=None):
        skip = handle_empty_backlog(
            config={},
            repo="owner/anything",
            project_id=99,
            workspace=str(workspace),
            run_id="test-3",
        )
    assert skip == "no-eligible-issues-no-vision"
