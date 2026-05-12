"""Payload-parity tests for agent.station_orchestrator.RunDriver (#361).

Verifies the RunDriver-emitted ``run_start`` and ``run_complete`` payloads
match the field set the legacy bash entry point used to ship. Bash field
inventory:

- ``run_start``: project_count, max_concurrent, concurrent_group_id, log_file
- ``run_complete``: status, exit_code, tokens_input, tokens_output,
  tokens_total, turns, duration_ms
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


_CONFIG = {
    "projects": [
        {"enabled": True, "repo": "x/y"},
        {"enabled": True, "repo": "a/b"},
        {"enabled": False, "repo": "ignored/proj"},
    ],
    "limits": {"max_concurrent_employees": 3},
}


def _write_config(tmp_path: Path) -> Path:
    p = tmp_path / "manager-config.json"
    p.write_text(json.dumps(_CONFIG))
    return p


def _emit_call(emit_mock, event_name: str):
    return next(c for c in emit_mock.call_args_list if c.args[0] == event_name)


def test_run_start_payload_carries_full_bash_field_set(tmp_path):
    """run_start MUST emit project_count / max_concurrent /
    concurrent_group_id / log_file — same set the bash used to send.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-1",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        driver.run()

    start = _emit_call(mock_emit, "run_start")
    payload = start.kwargs.get("payload", {})
    # Enabled projects only (2 of 3 above).
    assert payload["project_count"] == 2
    assert payload["max_concurrent"] == 3
    assert payload["concurrent_group_id"] == "group-test-1"
    assert payload["log_file"] == str(tmp_path / "run-test-1.log")


def test_run_complete_payload_includes_telemetry_from_bash_dump(tmp_path):
    """When the bash shim writes its telemetry JSON file, the driver MUST
    read it and include the tokens/turns in the run_complete payload.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    # Pre-stage the telemetry dump as if a bash --internal-iterate run wrote it.
    telem_path = tmp_path / "run-test-2-telemetry.json"
    telem_path.write_text(json.dumps({
        "exit_code": 0,
        "tokens_input": 12345,
        "tokens_output": 6789,
        "tokens_total": 19134,
        "turns": 42,
        "duration_ms": 90000,
    }))

    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-2",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        driver.run()

    complete = _emit_call(mock_emit, "run_complete")
    payload = complete.kwargs.get("payload", {})
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0
    assert payload["tokens_input"] == 12345
    assert payload["tokens_output"] == 6789
    assert payload["tokens_total"] == 19134
    assert payload["turns"] == 42
    # duration_ms is wall-clock by Python (not copied from bash's count).
    assert payload["duration_ms"] >= 0


def test_run_complete_falls_back_to_zero_telemetry_when_dump_missing(tmp_path):
    """If the bash dump never appeared (e.g. the bash crashed before its
    EXIT trap fired), the driver MUST still ship run_complete with zero
    counters — never omit fields the dashboard expects.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-3",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        driver.run()

    payload = _emit_call(mock_emit, "run_complete").kwargs.get("payload", {})
    for k in ("tokens_input", "tokens_output", "tokens_total", "turns"):
        assert payload[k] == 0, f"{k} should default to 0 when dump missing"
    assert payload["exit_code"] == 0
    assert payload["status"] == "completed"


def test_run_complete_status_interrupted_on_keyboard_interrupt(tmp_path):
    """SIGINT (or SIGTERM mapped to KeyboardInterrupt) MUST surface as
    status=interrupted with exit code 130 — never collapse to error/failed.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects",
               side_effect=KeyboardInterrupt), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-4",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        rc = driver.run()

    assert rc == 130
    payload = _emit_call(mock_emit, "run_complete").kwargs.get("payload", {})
    assert payload["status"] == "interrupted"
    assert payload["exit_code"] == 130


def test_run_complete_status_interrupted_on_child_exit_130(tmp_path):
    """iterate_projects returning 130 (child was killed by SIGINT) MUST
    also map to status=interrupted, not status=failed.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=130), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-5",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        rc = driver.run()

    assert rc == 130
    payload = _emit_call(mock_emit, "run_complete").kwargs.get("payload", {})
    assert payload["status"] == "interrupted"


def test_run_id_is_normalized_to_run_prefix_on_the_wire(tmp_path):
    """The webhook wire convention is ``run-<id>``. The driver MUST send
    that form regardless of whether the caller passed the prefix.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    # Caller passes without the prefix.
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="bareid-1",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        driver.run()

    for call in mock_emit.call_args_list:
        assert call.kwargs.get("run_id") == "run-bareid-1"


def test_run_complete_status_failed_on_nonzero_non_130_exit(tmp_path):
    """A generic non-zero, non-130 exit code from iterate_projects is a
    failure (e.g. config error), not an interrupt.
    """
    from agent.station_orchestrator import RunDriver

    cfg_path = _write_config(tmp_path)
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=1), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-6",
                           config_path=str(cfg_path),
                           workspaces_dir=str(tmp_path))
        rc = driver.run()

    assert rc == 1
    payload = _emit_call(mock_emit, "run_complete").kwargs.get("payload", {})
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1


def test_telemetry_init_handles_missing_or_invalid_config(tmp_path):
    """RunDriver.__init__ must NOT crash when the config path is bogus —
    it logs a warning and degrades to default project_count=0,
    max_concurrent=1. Mirrors the bash's lenient json_get behavior.
    """
    from agent.station_orchestrator import RunDriver

    with patch("agent.station_orchestrator.emit"), \
         patch("agent.project_loop.iterate_projects", return_value=0), \
         patch.dict("os.environ", {"STATION_LOG_DIR": str(tmp_path)}):
        driver = RunDriver(run_id="run-test-7",
                           config_path=str(tmp_path / "nonexistent.json"),
                           workspaces_dir=str(tmp_path))
        assert driver.telemetry.project_count == 0
        assert driver.telemetry.max_concurrent == 1
        # Should not raise on run() either.
        rc = driver.run()
        assert rc == 0
