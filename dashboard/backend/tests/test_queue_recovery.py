"""Tests for agent.queue_recovery (issue #383 bash port)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_purge_completed_items_calls_complete(monkeypatch):
    from agent import queue_recovery

    fake_items = [
        {"id": "q1", "status": "running", "run_id": "run-old", "issue_number": 1},
        {"id": "q2", "status": "running", "run_id": "run-cur", "issue_number": 2},
    ]

    list_calls = MagicMock(return_value=fake_items)
    complete_calls = MagicMock(return_value=None)
    is_alive = MagicMock(side_effect=lambda rid: rid == "run-cur")

    monkeypatch.setattr(queue_recovery, "_list_running_items", list_calls)
    monkeypatch.setattr(queue_recovery, "_run_is_alive", is_alive)
    monkeypatch.setattr(queue_recovery, "_mark_item", complete_calls)

    queue_recovery.purge_and_recover("run-cur")

    # q1 belongs to a dead run → marked recovered/failed.
    # q2 belongs to the current run → left alone.
    called_ids = [call.args[0] for call in complete_calls.call_args_list]
    assert "q1" in called_ids, "queue_recovery must mark orphan items"
    assert "q2" not in called_ids, "queue_recovery must not touch current-run items"


def test_resume_paused_items(monkeypatch):
    from agent import queue_recovery

    paused = [{"id": "qp1", "status": "paused", "issue_number": 5}]
    monkeypatch.setattr(queue_recovery, "_list_paused_items", MagicMock(return_value=paused))
    resume_calls = MagicMock()
    monkeypatch.setattr(queue_recovery, "_mark_item", resume_calls)

    queue_recovery.resume_paused()

    called = [c.args for c in resume_calls.call_args_list]
    assert any(args[0] == "qp1" for args in called), "must mark paused items as pending/running"


def test_ignore_current_run(monkeypatch):
    """Items belonging to the *current* run_id must never be touched by purge."""
    from agent import queue_recovery

    items = [{"id": "active", "status": "running", "run_id": "run-active"}]
    monkeypatch.setattr(queue_recovery, "_list_running_items", MagicMock(return_value=items))
    monkeypatch.setattr(queue_recovery, "_run_is_alive", MagicMock(return_value=True))
    mark = MagicMock()
    monkeypatch.setattr(queue_recovery, "_mark_item", mark)

    queue_recovery.purge_and_recover("run-active")

    assert mark.call_count == 0, "no items should be marked when run is alive and current"
