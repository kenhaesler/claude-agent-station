"""Tests for agent.station_orchestrator._force_exit_with_cleanup.

After orchestrate() returns, the SDK's transport teardown does not
reliably reap its bundled CLI subprocess — production has seen the
bundled CLI keep running, firing failed PreToolUse/PostToolUse hook
callbacks against a closed stdin, flooding the bash log stream long
after the Python orchestrator was "done". See run-20260513T044331Z.

main() now calls _force_exit_with_cleanup which:
1. Enumerates this process's direct children via /proc walk
2. SIGTERMs them so the kernel reaps them on our exit
3. Calls os._exit (bypasses asyncio.run / SDK teardown that has been
   observed to hang)

These tests cover the SIGTERM enumeration and the failure-tolerant
shape — they cannot easily cover os._exit itself without spawning a
subprocess.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import patch


def test_signals_only_direct_children(tmp_path):
    """When called, the cleanup MUST signal only this process's
    direct children — NOT grandchildren, NOT unrelated processes
    that happen to have the same PPid in some other tree.
    """
    from agent.station_orchestrator import _force_exit_with_cleanup

    my_pid = os.getpid()
    # Fake /proc tree: pid 1000 is our child, pid 2000 is NOT.
    proc_entries = ["1000", "2000", "1", "self"]

    def _fake_open(path, *args, **kwargs):
        # Only respond to status files we expect
        if path == "/proc/1000/status":
            from io import StringIO
            return StringIO(f"Name: child\nState: S\nPPid: {my_pid}\n")
        if path == "/proc/2000/status":
            from io import StringIO
            return StringIO("Name: unrelated\nState: S\nPPid: 1\n")
        raise FileNotFoundError(path)

    killed: list[int] = []

    def _fake_kill(pid, sig):
        assert sig == signal.SIGTERM, (
            f"cleanup must use SIGTERM, not {sig!r} — graceful is essential "
            f"because the bundled SDK CLI is a normal child process and "
            f"we don't want to leave half-written files."
        )
        killed.append(pid)

    def _fake_exit(code):
        raise SystemExit(code)

    with patch("agent.station_orchestrator.os.listdir", return_value=proc_entries), \
         patch("builtins.open", side_effect=_fake_open), \
         patch("agent.station_orchestrator.os.kill", side_effect=_fake_kill), \
         patch("agent.station_orchestrator.os._exit", side_effect=_fake_exit):
        try:
            _force_exit_with_cleanup(0)
        except SystemExit:
            pass

    assert killed == [1000], (
        f"Cleanup signaled wrong PIDs. Expected only [1000] (our child); "
        f"got {killed!r}. Either it picked up an unrelated process (PPid "
        f"mismatch leaked) or skipped our actual child."
    )


def test_swallows_oserror_from_dead_processes(tmp_path):
    """Children can die between /proc enumeration and os.kill. The
    cleanup MUST swallow the resulting OSError so it can keep
    signaling other children.
    """
    from agent.station_orchestrator import _force_exit_with_cleanup

    my_pid = os.getpid()

    def _fake_open(path, *args, **kwargs):
        from io import StringIO
        if path.startswith("/proc/") and path.endswith("/status"):
            return StringIO(f"Name: child\nPPid: {my_pid}\n")
        raise FileNotFoundError(path)

    kill_attempts: list[int] = []

    def _flaky_kill(pid, sig):
        kill_attempts.append(pid)
        if pid == 1000:
            raise ProcessLookupError("No such process")
        # pid 2000 succeeds

    with patch("agent.station_orchestrator.os.listdir",
               return_value=["1000", "2000"]), \
         patch("builtins.open", side_effect=_fake_open), \
         patch("agent.station_orchestrator.os.kill", side_effect=_flaky_kill), \
         patch("agent.station_orchestrator.os._exit"):
        _force_exit_with_cleanup(0)

    assert kill_attempts == [1000, 2000], (
        f"Cleanup gave up after the first failure. Got: {kill_attempts!r}. "
        f"It must keep going so one dead child doesn't leave others "
        f"unsignaled."
    )


def test_eventually_calls_os_exit_with_provided_code():
    """The whole point is to bypass the asyncio.run teardown that has
    been observed to hang. Cleanup MUST end in os._exit with the
    correct exit code — not return normally (which would let asyncio.run
    cleanup run).
    """
    from agent.station_orchestrator import _force_exit_with_cleanup

    actual_exit_code: list[int] = []

    def _record_exit(code):
        actual_exit_code.append(code)
        raise SystemExit(code)

    with patch("agent.station_orchestrator.os.listdir", return_value=[]), \
         patch("agent.station_orchestrator.os._exit", side_effect=_record_exit):
        try:
            _force_exit_with_cleanup(42)
        except SystemExit:
            pass

    assert actual_exit_code == [42], (
        f"_force_exit_with_cleanup did not call os._exit(42); got: {actual_exit_code!r}"
    )
