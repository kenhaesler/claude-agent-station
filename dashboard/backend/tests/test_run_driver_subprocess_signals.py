"""Real-subprocess SIGINT / SIGTERM tests for RunDriver (#361).

The mocked tests in ``test_run_driver_payload.py`` exercise the
``except KeyboardInterrupt`` branch by patching ``iterate_projects``
with ``side_effect=KeyboardInterrupt``. That validates the catch-and-
emit logic but does NOT exercise:

- Python's actual signal-handler installation (``signal.signal(SIGTERM, …)``)
- Real SIGINT/SIGTERM delivery to a running interpreter
- The signal-handler-raises-KeyboardInterrupt path
- ``iterate_projects``' Popen-based SIGTERM forwarding to a child

This file spawns the driver as a real subprocess, sends a real signal,
and reads back the emitted webhook events from a recorder file. Slow
(~2 s per test) but the only way to verify the signal contract end-
to-end.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Repo root: pyproject.toml lives here and ``pythonpath = ["."]`` puts
# this on sys.path for pytest. The subprocess needs the same.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _driver_script(events_log: Path, cfg_path: Path, sleep_for: int = 30) -> str:
    """Build the inline -c script the test subprocess will run.

    Replaces ``agent.webhook_emitter.emit`` (and the same name re-exported
    in station_orchestrator) with a recorder that appends each event to
    ``events_log`` as JSON-lines. Replaces ``iterate_projects`` with a
    long sleep so the test has time to send the signal after the driver
    has installed its signal handlers.
    """
    return f"""
import json, sys, time
sys.path.insert(0, {str(_REPO_ROOT)!r})

EVENTS = {str(events_log)!r}

def _recording_emit(event, *, run_id, payload=None):
    with open(EVENTS, "a") as f:
        f.write(json.dumps({{
            "event": event,
            "run_id": run_id,
            "payload": payload or {{}},
        }}) + chr(10))
        f.flush()

# Patch BEFORE RunDriver is constructed. station_orchestrator.emit is
# the module-level name the driver actually calls (``from
# agent.webhook_emitter import emit`` at import time), so we have to
# patch the binding in station_orchestrator, not just webhook_emitter.
import agent.webhook_emitter
agent.webhook_emitter.emit = _recording_emit
import agent.station_orchestrator
agent.station_orchestrator.emit = _recording_emit

# Replace iterate_projects with a sleep long enough for the test to
# send the signal after RunDriver.run() has installed its handlers.
import agent.project_loop
def _slow_iterate(*a, **kw):
    time.sleep({sleep_for})
    return 0
agent.project_loop.iterate_projects = _slow_iterate

from agent.station_orchestrator import RunDriver
driver = RunDriver(
    run_id="run-sigtest",
    config_path={str(cfg_path)!r},
    workspaces_dir={str(cfg_path.parent)!r},
)
sys.exit(driver.run())
"""


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _wait_for_run_start(events_path: Path, timeout: float = 5.0) -> None:
    """Block until the subprocess has emitted run_start. That's our
    signal that the signal handlers have been installed and the driver
    is in iterate_projects (i.e. sleeping). Polling instead of a fixed
    sleep keeps the test fast on a warm box and reliable on a cold one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e["event"] == "run_start" for e in _read_events(events_path)):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"driver did not emit run_start within {timeout}s — handler "
        f"installation likely failed",
    )


@pytest.fixture
def driver_fixtures(tmp_path):
    """Common scaffolding: config file + events recorder file."""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "projects": [],
        "limits": {"max_concurrent_employees": 1},
    }))
    events = tmp_path / "events.jsonl"
    events.write_text("")  # touch
    return cfg, events, tmp_path


def _spawn_driver(cfg: Path, events: Path, tmp_path: Path,
                  sleep_for: int = 30) -> subprocess.Popen:
    """Launch the driver as a detached subprocess."""
    script = _driver_script(events, cfg, sleep_for=sleep_for)
    env = os.environ.copy()
    env["STATION_LOG_DIR"] = str(tmp_path)
    env.setdefault("PYTHONPATH", str(_REPO_ROOT))
    return subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_real_sigint_marks_run_interrupted_with_exit_130(driver_fixtures):
    """SIGINT to the driver MUST exit 130 AND emit run_complete with
    status=interrupted. This exercises Python's default SIGINT->KI
    behavior on the real interpreter, not a mocked KI."""
    cfg, events, tmp_path = driver_fixtures
    proc = _spawn_driver(cfg, events, tmp_path)
    try:
        _wait_for_run_start(events)
        proc.send_signal(signal.SIGINT)
        exit_code = proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert exit_code == 130, (
        f"SIGINT must exit 130 (POSIX convention); got {exit_code}. "
        f"stderr: {proc.stderr.read().decode()[:500] if proc.stderr else ''}"
    )
    emitted = _read_events(events)
    event_names = [e["event"] for e in emitted]
    assert "run_start" in event_names
    assert "run_complete" in event_names, (
        f"finally clause did not fire — emitted: {event_names}"
    )
    complete = next(e for e in emitted if e["event"] == "run_complete")
    assert complete["payload"]["status"] == "interrupted"
    assert complete["payload"]["exit_code"] == 130


def test_real_sigterm_marks_run_interrupted_with_exit_130(driver_fixtures):
    """SIGTERM (launcher /stop, _zombie_reaper) MUST be mapped to the
    same interrupted-exit-130 contract via the driver's
    _install_signal_handlers. This is the acceptance criterion from
    issue #361 ('docker compose kill --signal SIGINT cas-agent results
    in the dashboard marking the run interrupted') — verified with
    actual signal delivery, not patched KI."""
    cfg, events, tmp_path = driver_fixtures
    proc = _spawn_driver(cfg, events, tmp_path)
    try:
        _wait_for_run_start(events)
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert exit_code == 130, (
        f"SIGTERM-mapped-to-KI must exit 130; got {exit_code}. "
        f"stderr: {proc.stderr.read().decode()[:500] if proc.stderr else ''}"
    )
    emitted = _read_events(events)
    complete = next(e for e in emitted if e["event"] == "run_complete")
    assert complete["payload"]["status"] == "interrupted"
    assert complete["payload"]["exit_code"] == 130
