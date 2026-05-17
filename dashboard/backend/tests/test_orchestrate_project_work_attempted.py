"""Tests for the work_attempted bool in orchestrate_project's return.

Spec: docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md
Issues: #446, #447
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These tests verify the new return contract introduced by Task 1 of the
# idle-run-semantics plan. They mock the picker so the function returns
# from its early-exit branch (no eligible issues) without booting the
# SDK session, OR from a path that did open the session.


async def test_orchestrate_project_returns_work_attempted_false_when_no_eligible_issues(
    tmp_path,
):
    """When the picker finds no eligible issues, orchestrate_project must
    return (exit_code, None, False) — no SDK session was opened."""
    from agent import station_orchestrator as so

    project = {"repo": "test/repo", "id": 1, "mode": "full"}
    config = {
        "projects": [project],
        "logging": {"log_dir": str(tmp_path)},
    }

    # Force the no-eligible-issues short-circuit:
    # fetch_eligible_issues returns [] AND claim_pending_queue_items returns []
    # → handle_empty_backlog runs → function returns at the early-exit point.
    # orchestrate_project ignores handle_empty_backlog's return value, so a
    # ``lambda: None`` patch reflects the real contract — see review of
    # commit 93d8ddd, Finding 3.
    with patch.object(so, "fetch_eligible_issues", return_value=[]), \
         patch.object(so, "claim_pending_queue_items", AsyncMock(return_value=[])), \
         patch.object(so, "handle_empty_backlog", lambda *a, **kw: None), \
         patch.object(so, "_ensure_workspace", return_value=None):
        result = await so.orchestrate_project(project, config, "test-run", str(tmp_path))

    assert len(result) == 3, f"Expected 3-tuple, got {len(result)}-tuple"
    exit_code, stream_state, work_attempted = result
    assert work_attempted is False, (
        "work_attempted must be False when no eligible issues found"
    )
    assert stream_state is None
    assert exit_code == 0


async def test_orchestrate_project_returns_work_attempted_true_when_session_opened(
    monkeypatch, tmp_path,
):
    """When the SDK session opens (eligible issues found), work_attempted
    must be True even if the session later errors out."""
    from agent import station_orchestrator as so

    project = {"repo": "owner/repo", "id": 1, "mode": "full"}
    config = {
        "projects": [project],
        "limits": {"max_concurrent_employees": 1},
        "models": {},
        "logging": {"log_dir": str(tmp_path)},
    }
    fake_issue = {"number": 1, "title": "test", "body": "", "labels": []}

    # Build a minimal fake ClaudeSDKClient that enters/exits without error
    # and yields a result message so the session completes cleanly.
    init_msg = MagicMock(spec=so.SystemMessage)
    init_msg.subtype = "init"
    init_msg.session_id = "sess-1"

    result_msg = MagicMock(spec=so.ResultMessage)
    result_msg.session_id = "sess-1"
    result_msg.result = "All teammates have completed. Final summary."
    result_msg.is_error = False
    result_msg.duration_ms = 100
    result_msg.num_turns = 1

    class _FakeClient:
        def __init__(self, *, options=None):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def query(self, prompt: str):
            pass

        async def receive_response(self):
            for msg in [init_msg, result_msg]:
                yield msg

        async def interrupt(self):
            pass

    # Picker returns one issue → session opens. Mock everything downstream
    # to a no-op success so we just verify the work_attempted=True signal.
    # Explicit patches for every side-effect inside the session body
    # (build_run_complete_server, _ensure_review_package,
    # _synthesize_employee_report) prevent this test from depending on the
    # ``except Exception`` guards that wrap them — see review of commit
    # 93d8ddd, Finding 2.
    monkeypatch.setattr(so, "ClaudeSDKClient", lambda options=None: _FakeClient(options=options))
    monkeypatch.setattr(so, "_ensure_workspace", lambda *a, **k: None)
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    monkeypatch.setattr(so, "fetch_eligible_issues", lambda *a, **k: [fake_issue])
    monkeypatch.setattr(so, "claim_pending_queue_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(so, "load_vision", lambda *a, **k: None)
    monkeypatch.setattr(so, "_combined_rank_issues", lambda issues, **k: issues)
    monkeypatch.setattr(so, "build_team_prompt", lambda *a, **k: "test prompt")
    monkeypatch.setattr(so, "build_followup_prompt", lambda *a, **k: "followup prompt")
    monkeypatch.setattr(so, "handle_stream_event", AsyncMock())
    monkeypatch.setattr(so, "_control_poll_loop", AsyncMock())
    monkeypatch.setattr(so, "build_run_complete_server", lambda *a, **k: MagicMock())
    monkeypatch.setattr(so, "_ensure_review_package", lambda *a, **k: None)
    monkeypatch.setattr(so, "_synthesize_employee_report", lambda *a, **k: None)
    monkeypatch.setattr(so.asyncio, "sleep", AsyncMock())
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: MagicMock(returncode=0, stderr=""))

    result = await so.orchestrate_project(project, config, "test-run", str(tmp_path))

    assert len(result) == 3
    _exit_code, _stream_state, work_attempted = result
    assert work_attempted is True, (
        "work_attempted must be True once the SDK session is opened"
    )
