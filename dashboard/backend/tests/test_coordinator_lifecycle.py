"""Tests for agent/coordinator_lifecycle.py (issue #349, sub-PR 5b)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _reset_open_tasks():
    from agent import coordinator_lifecycle as cl
    cl._open_tasks.clear()
    yield
    cl._open_tasks.clear()


def test_create_task_posts_to_queue_api():
    from agent.coordinator_lifecycle import create_task
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"id": "t-1"}
        mock_post.return_value = resp
        task_id = create_task(
            run_id="run-1", project_repo="x/y", issue_number=42,
            employee_index=0,
        )
        assert task_id == "t-1"
        body = mock_post.call_args.kwargs["json"]
        assert body["run_id"] == "run-1"
        assert body["project_repo"] == "x/y"


def test_atexit_handler_fails_pending_tasks():
    """If complete_task is never called, atexit must fail-finalize the
    task so /api/runs/active-employees does not surface a zombie."""
    from agent import coordinator_lifecycle as cl
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"id": "t-doomed"}
        mock_post.return_value = resp
        cl.create_task(run_id="run-2", project_repo="x/y",
                       issue_number=1, employee_index=0)

    # Simulate the atexit fire
    with patch("agent.coordinator_lifecycle.httpx.put") as mock_put:
        resp_put = MagicMock(status_code=200)
        mock_put.return_value = resp_put
        cl._finalize_orphans()
        assert mock_put.called
        url = mock_put.call_args.args[0]
        assert "/api/coordinator/tasks/t-doomed" in url
        body = mock_put.call_args.kwargs["json"]
        assert body["status"] == "orphaned"


def test_complete_task_clears_open_set():
    from agent import coordinator_lifecycle as cl
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"id": "t-clear"}
        mock_post.return_value = resp
        tid = cl.create_task(run_id="run-3", project_repo="x/y",
                             issue_number=2, employee_index=0)
    with patch("agent.coordinator_lifecycle.httpx.put") as mock_put:
        mock_put.return_value = MagicMock(status_code=200)
        cl.complete_task(tid)
    # Simulate atexit — task is no longer in the open set, no PUT
    with patch("agent.coordinator_lifecycle.httpx.put") as mock_put_atexit:
        cl._finalize_orphans()
        assert not mock_put_atexit.called


def test_fail_task_uses_failed_status():
    from agent import coordinator_lifecycle as cl
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"id": "t-fail"}
        mock_post.return_value = resp
        tid = cl.create_task(run_id="run-4", project_repo="x/y",
                             issue_number=3, employee_index=0)
    with patch("agent.coordinator_lifecycle.httpx.put") as mock_put:
        mock_put.return_value = MagicMock(status_code=200)
        cl.fail_task(tid, reason="boom")
        body = mock_put.call_args.kwargs["json"]
        assert body["status"] == "failed"
        assert body["result_summary"] == "boom"
