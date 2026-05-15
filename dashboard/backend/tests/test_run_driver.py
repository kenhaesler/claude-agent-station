"""Tests for agent.station_orchestrator.RunDriver (issue #349, sub-PR 5c)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_run_driver_emits_run_start_then_run_complete_on_success():
    """The driver MUST emit run_start at entry and run_complete at exit
    via webhook_emitter, replacing the bash EXIT-trap construct."""
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit") as mock_emit, \
         patch("agent.project_loop.iterate_projects", return_value=(0, None)):
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
         patch("agent.project_loop.iterate_projects", return_value=(0, None)):
        driver = RunDriver(run_id="run-driver-3",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        driver.run()
    complete_call = next(c for c in mock_emit.call_args_list
                         if c.args[0] == "run_complete")
    assert complete_call.kwargs.get("payload", {}).get("status") == "completed"


def test_run_driver_passes_bare_run_id_to_iterate_projects():
    """Regression: iterate_projects (and everything downstream — post_webhook,
    the launcher /webhook-tick payload, write_digest, verdict files) builds
    wire-format ids by prepending ``run-`` to its input. The driver receives
    ``--run-id run-<ts>`` from the dashboard's pre-allocated trigger flow,
    so passing self.run_id raw produces ``run-run-<ts>`` everywhere, the
    launcher 404s the tick, and the reaper kills the runner at 120s. See
    live run-20260515T234700Z post-mortem.
    """
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit"), \
         patch("agent.project_loop.iterate_projects",
               return_value=(0, None)) as mock_iter:
        driver = RunDriver(run_id="run-20260515T234700Z",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        driver.run()
    args, _ = mock_iter.call_args
    assert args[0] == "20260515T234700Z", (
        f"iterate_projects must receive the BARE run_id (no 'run-' prefix); "
        f"got {args[0]!r}. Without this, post_webhook payloads carry "
        f"'run-run-<ts>' run_ids and the launcher rejects every tick."
    )


def test_run_driver_bare_run_id_passes_through_unchanged():
    """Legacy callers (bash systemd shim) pass the bare timestamp without
    the ``run-`` prefix. iterate_projects must still receive that bare form."""
    from agent.station_orchestrator import RunDriver
    with patch("agent.station_orchestrator.emit"), \
         patch("agent.project_loop.iterate_projects",
               return_value=(0, None)) as mock_iter:
        driver = RunDriver(run_id="20260515T234700Z",
                           config_path="/tmp/cfg.json",
                           workspaces_dir="/tmp/ws")
        driver.run()
    args, _ = mock_iter.call_args
    assert args[0] == "20260515T234700Z"
