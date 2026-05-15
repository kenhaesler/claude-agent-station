"""Launcher runs map (#386)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import launcher


def test_runners_dict_starts_empty():
    launcher._runners.clear()
    assert launcher._runners == {}


def test_runner_handle_stores_metadata():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-abc",
        container_name="cas-runner-abc",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo="x/y",
    )
    launcher._runners[handle.run_id] = handle
    assert launcher._runners["run-abc"].container_name == "cas-runner-abc"
    assert launcher._runners["run-abc"].project_repo == "x/y"
