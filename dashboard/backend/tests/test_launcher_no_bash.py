"""Per #383, the launcher no longer offers a bash entry point."""
from __future__ import annotations


def test_launcher_does_not_reference_use_bash_launcher():
    import inspect
    from agent import launcher
    src = inspect.getsource(launcher)
    assert "STATION_LAUNCHER_USE_BASH" not in src
    assert "USE_BASH_LAUNCHER" not in src
    assert "RUN_MANAGER" not in src


def test_launcher_command_is_python_only():
    import inspect
    from agent import launcher
    src = inspect.getsource(launcher._spawn_run_manager)
    assert "bash" not in src.lower() or "run-manager.sh" not in src
    assert "station_orchestrator" in src and "--driver" in src
