"""Tests for agent/webhook_emitter.py (issue #349, sub-PR 5a)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def _dashboard_call(mock_post):
    """Pick out the dashboard webhook call from the captured posts.
    Since the emitter also pings the launcher's /webhook-tick (a
    second POST), call_args alone now points to that ping, not the
    dashboard. Use the call list and filter by URL substring."""
    for call in mock_post.call_args_list:
        url = call.args[0] if call.args else ""
        if "/api/webhook/run-event" in url:
            return call
    raise AssertionError(f"no dashboard webhook call seen; saw: {[c.args for c in mock_post.call_args_list]}")


def _launcher_call(mock_post):
    """Pick out the launcher /webhook-tick call. Counterpart of
    :func:`_dashboard_call`."""
    for call in mock_post.call_args_list:
        url = call.args[0] if call.args else ""
        if "/webhook-tick" in url:
            return call
    raise AssertionError(f"no launcher tick call seen; saw: {[c.args for c in mock_post.call_args_list]}")


def test_emit_run_start_posts_to_webhook():
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-test-1", payload={"project": "x/y"})
        assert mock_post.called
        call = _dashboard_call(mock_post)
        url = call.args[0]
        body = call.kwargs["json"]
        assert "/api/webhook/run-event" in url
        assert body["event"] == "run_start"
        assert body["run_id"] == "run-test-1"
        assert body["project"] == "x/y"


def test_emit_retries_on_5xx():
    """3 attempts with exponential backoff; eventual success returns cleanly."""
    from agent.webhook_emitter import emit
    responses = [
        MagicMock(status_code=500, text="boom"),
        MagicMock(status_code=500, text="boom"),
        MagicMock(status_code=200, text="ok"),
    ]
    with patch("agent.webhook_emitter.httpx.post", side_effect=responses), \
         patch("agent.webhook_emitter.time.sleep") as mock_sleep:
        emit("run_complete", run_id="run-test-2",
             payload={"status": "completed"})
        # Should have slept twice (0.5s, 1s) between the three attempts
        assert mock_sleep.call_count == 2


def test_emit_does_not_raise_on_final_failure():
    """Orchestrator must never be killed by a dashboard outage."""
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post",
               side_effect=[MagicMock(status_code=500, text="boom")] * 3), \
         patch("agent.webhook_emitter.time.sleep"):
        # No exception
        emit("run_complete", run_id="run-test-3",
             payload={"status": "completed"})


def test_emit_does_not_retry_on_4xx():
    """4xx is a client error; retrying won't help. (The emitter still
    pings the launcher's /webhook-tick once at the end, hence two
    total POSTs: one dashboard attempt + one launcher ping.)"""
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        # Dashboard 400 → no retry, no further dashboard calls.
        # Launcher ping is a separate URL — fire-and-forget.
        mock_post.return_value = MagicMock(status_code=400, text="bad")
        emit("run_complete", run_id="run-test-4",
             payload={"status": "completed"})
    dashboard_calls = [c for c in mock_post.call_args_list
                       if "/api/webhook/run-event" in (c.args[0] if c.args else "")]
    assert len(dashboard_calls) == 1, "expected exactly one dashboard attempt"


def test_emit_uses_x_webhook_token_header():
    """The dashboard webhook router expects X-Webhook-Token. If a future
    refactor renames the header again, fail loudly."""
    import os
    from unittest.mock import patch, MagicMock
    from agent.webhook_emitter import emit
    with patch.dict(os.environ, {"STATION_WEBHOOK_SECRET": "s3cr3t"}), \
         patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-h1", payload={})
        headers = _dashboard_call(mock_post).kwargs["headers"]
        assert "X-Webhook-Token" in headers
        assert headers["X-Webhook-Token"] == "s3cr3t"
        # The wrong name must NOT be present
        assert "X-Webhook-Secret" not in headers


def test_emit_omits_token_header_when_secret_unset():
    """No secret → no header sent."""
    import os
    from unittest.mock import patch, MagicMock
    from agent.webhook_emitter import emit
    with patch.dict(os.environ, {}, clear=False), \
         patch("agent.webhook_emitter.httpx.post") as mock_post:
        # Force unset
        os.environ.pop("STATION_WEBHOOK_SECRET", None)
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-h2", payload={})
        headers = _dashboard_call(mock_post).kwargs["headers"]
        assert "X-Webhook-Token" not in headers


def test_module_importable_from_repo_root_via_pythonpath():
    """Regression test for the run-manager.sh PYTHONPATH hotfix (issue #349).

    PR #352 introduced webhook_event() with PYTHONPATH=$SCRIPT_DIR/.. which
    resolves to agent/. Python needs the PARENT of agent/ (the repo root) to
    resolve `import agent`. On systemd deployments the cwd is the workspaces
    dir (not the repo root), so the import fails silently inside `|| true`,
    dropping every bash-emitted webhook event.

    This test simulates the corrected bash wrapper: PYTHONPATH=$SCRIPT_DIR/../..
    (two levels up from agent/scripts/), cwd=/tmp, and asserts the module is
    importable.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    # SCRIPT_DIR is agent/scripts; two levels up reaches the repo root
    script_dir = Path(__file__).resolve().parents[3] / "agent" / "scripts"
    pythonpath = str((script_dir / ".." / "..").resolve())

    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [sys.executable, "-c", "import agent.webhook_emitter; print('ok')"],
        cwd="/tmp",  # NOT the repo root — simulates systemd deployment cwd
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"agent.webhook_emitter not importable from cwd=/tmp with "
        f"PYTHONPATH={pythonpath}; stderr: {result.stderr}"
    )
    assert "ok" in result.stdout


def test_old_pythonpath_fails_from_arbitrary_cwd():
    """Negative regression: the pre-fix PYTHONPATH ($SCRIPT_DIR/..) must NOT
    allow `import agent` from an arbitrary cwd. This documents that the
    one-level path was wrong and the fix is necessary."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Pre-fix: SCRIPT_DIR/.. resolves to agent/ itself, which doesn't contain
    # an `agent` package to import.
    script_dir = Path(__file__).resolve().parents[3] / "agent" / "scripts"
    wrong_pythonpath = str((script_dir / "..").resolve())

    env = {**os.environ, "PYTHONPATH": wrong_pythonpath}
    result = subprocess.run(
        [sys.executable, "-c", "import agent.webhook_emitter; print('ok')"],
        cwd="/tmp",  # NOT the repo root
        env=env,
        capture_output=True,
        text=True,
    )
    # With the wrong path and a non-repo cwd, the import must fail.
    assert result.returncode != 0, (
        "Expected import to fail with pre-fix PYTHONPATH but it succeeded; "
        "the test environment may have agent/ on sys.path via other means."
    )


def test_launcher_tick_includes_run_id_query_param():
    """Container-mode runners MUST pass ``run_id`` to /webhook-tick so
    the launcher updates the per-run ``handle.last_webhook_at`` in its
    ``_runners`` map. Without the param the launcher falls through to
    the legacy global timestamp and the container reaper SIGTERMs the
    runner at the 120s idle mark. Regression guard for the
    post-#386/#430 reaper-killing-active-runs bug.
    """
    from agent.webhook_emitter import emit

    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-tick-1", payload={})

    tick = _launcher_call(mock_post)
    assert tick.kwargs.get("params") == {"run_id": "run-tick-1"}, (
        "params={run_id=...} must be on the tick POST so the launcher's "
        "per-run handle gets updated"
    )


def test_launcher_tick_omits_run_id_param_when_not_supplied():
    """``_ping_launcher`` called without a ``run_id`` (e.g. from a
    bare-systemd inline runner) must send an empty params dict, not
    ``{"run_id": "None"}`` or similar — the launcher's handler
    interprets the absence of the param as legacy inline-mode and
    bumps the global timestamp instead.
    """
    from agent.webhook_emitter import _ping_launcher

    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        _ping_launcher()

    assert mock_post.call_args.kwargs.get("params") == {}
