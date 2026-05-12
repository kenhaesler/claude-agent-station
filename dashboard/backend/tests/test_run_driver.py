"""Tests for agent.station_orchestrator.RunDriver (issue #349, sub-PR 5c)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_run_driver_emits_run_start_then_run_complete_on_success():
    """The driver MUST emit run_start at entry and run_complete at exit
    via webhook_emitter, replacing the bash EXIT-trap construct."""
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0):
        driver = RunDriver(run_id="run-driver-1",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        rc = driver.run()
    assert rc == 0
    events = [c.args[0] for c in mock_emit.call_args_list]
    assert "run_start" in events
    assert "run_complete" in events
    # And the order is: start before complete
    assert events.index("run_start") < events.index("run_complete")


def test_run_driver_emits_run_complete_even_on_exception():
    """Try/finally invariant: if iterate_projects raises, the driver
    must STILL emit run_complete (with status=error) before propagating."""
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects",
               side_effect=RuntimeError("boom")):
        driver = RunDriver(run_id="run-driver-2",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        rc = driver.run()
    # Driver should not propagate; returns non-zero
    assert rc != 0
    events = [c.args[0] for c in mock_emit.call_args_list]
    assert "run_complete" in events
    # The run_complete event should carry status=error
    complete_call = next(c for c in mock_emit.call_args_list
                         if c.args[0] == "run_complete")
    assert complete_call.kwargs.get("payload", {}).get("status") == "error"


def test_run_driver_emits_run_complete_with_completed_status_on_success():
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=0):
        driver = RunDriver(run_id="run-driver-3",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        driver.run()
    complete_call = next(c for c in mock_emit.call_args_list
                         if c.args[0] == "run_complete")
    assert complete_call.kwargs.get("payload", {}).get("status") == "completed"
