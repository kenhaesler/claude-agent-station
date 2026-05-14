"""Tests for RunDriver telemetry without the bash JSON file (issue #383)."""
from __future__ import annotations


def test_rundriver_has_no_read_bash_telemetry():
    from agent import station_orchestrator as so
    assert not hasattr(so.RunDriver, "_read_bash_telemetry"), (
        "_read_bash_telemetry is removed in #383 — bash no longer writes the JSON dump"
    )


def test_rundriver_has_finalize_telemetry():
    from agent import station_orchestrator as so
    assert hasattr(so.RunDriver, "_finalize_telemetry"), (
        "RunDriver must provide _finalize_telemetry to copy counters in-process"
    )


def test_finalize_telemetry_copies_stream_state_counters():
    from agent import station_orchestrator as so

    class _State:
        tokens_in = 100
        tokens_out = 50
        turns = 7

    driver = so.RunDriver(run_id="run-x", config_path="/dev/null", workspaces_dir="/tmp")
    driver._finalize_telemetry(_State())
    assert driver.telemetry.tokens_input == 100
    assert driver.telemetry.tokens_output == 50
    assert driver.telemetry.turns == 7
    assert driver.telemetry.tokens_total == 150
