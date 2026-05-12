"""Tests for agent/webhook_emitter.py (issue #349, sub-PR 5a)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def test_emit_run_start_posts_to_webhook():
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        emit("run_start", run_id="run-test-1", payload={"project": "x/y"})
        assert mock_post.called
        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
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
    """4xx is a client error; retrying won't help."""
    from agent.webhook_emitter import emit
    with patch("agent.webhook_emitter.httpx.post",
               side_effect=[MagicMock(status_code=400, text="bad")]) as mock_post:
        emit("run_complete", run_id="run-test-4",
             payload={"status": "completed"})
        assert mock_post.call_count == 1


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
        headers = mock_post.call_args.kwargs["headers"]
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
        headers = mock_post.call_args.kwargs["headers"]
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
