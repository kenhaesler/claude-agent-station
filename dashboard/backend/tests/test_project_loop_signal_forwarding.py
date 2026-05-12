"""Tests for SIGTERM forwarding in agent.project_loop.iterate_projects (#361).

The Python driver maps SIGTERM to KeyboardInterrupt at its own signal
handler. ``iterate_projects`` must then forward the signal to the bash
child so the bash EXIT trap fires (the trap is what writes the
telemetry dump RunDriver reads in ``_read_bash_telemetry``). Without
this forwarding the bash would keep running, orphaned, after Python
had already emitted ``run_complete``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class _FakeProc:
    """Drop-in Popen stand-in. Each method records its calls so the
    test can assert the SIGTERM → wait → SIGKILL ladder shape.
    """

    def __init__(self, *, wait_behavior, terminate_behavior=None,
                 kill_behavior=None, pid: int = 4242) -> None:
        self.pid = pid
        self._wait_behavior = list(wait_behavior)
        self._terminate_behavior = terminate_behavior
        self._kill_behavior = kill_behavior
        self.terminated = False
        self.killed = False
        self.poll_result = None  # None == still running
        self.wait_calls = 0

    def wait(self, timeout=None):  # noqa: ARG002 — kept for Popen parity
        self.wait_calls += 1
        if not self._wait_behavior:
            return 0
        outcome = self._wait_behavior.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def terminate(self) -> None:
        self.terminated = True
        if self._terminate_behavior is not None:
            self._terminate_behavior(self)

    def kill(self) -> None:
        self.killed = True
        if self._kill_behavior is not None:
            self._kill_behavior(self)

    def poll(self) -> int | None:
        return self.poll_result


@pytest.fixture
def fake_runmgr(monkeypatch, tmp_path):
    """Ensure the runmgr existence check passes without touching disk."""
    monkeypatch.setattr(
        "agent.project_loop.Path.exists",
        lambda self: True,
    )
    return tmp_path


def test_normal_path_returns_child_exit_code(fake_runmgr):
    """No signal: wait() returns the exit code directly."""
    from agent import project_loop

    fake = _FakeProc(wait_behavior=[0])
    with patch.object(project_loop.subprocess, "Popen", return_value=fake):
        rc = project_loop.iterate_projects(
            "run-1", "/tmp/cfg.json", str(fake_runmgr),
        )
    assert rc == 0
    assert fake.terminated is False
    assert fake.killed is False


def test_keyboard_interrupt_forwards_sigterm_then_re_raises(fake_runmgr):
    """When wait() raises KeyboardInterrupt (the driver's SIGTERM
    handler), we MUST call terminate() and wait for the bash to exit
    before re-raising. Otherwise the bash is orphaned and never writes
    its telemetry dump.
    """
    from agent import project_loop

    fake = _FakeProc(wait_behavior=[
        KeyboardInterrupt(),  # first wait — interrupted
        130,                  # second wait (after terminate) — bash exited
    ])
    with patch.object(project_loop.subprocess, "Popen", return_value=fake):
        with pytest.raises(KeyboardInterrupt):
            project_loop.iterate_projects(
                "run-1", "/tmp/cfg.json", str(fake_runmgr),
            )

    assert fake.terminated is True, "SIGTERM must be forwarded to bash"
    assert fake.wait_calls >= 2, "must wait for bash to exit after terminate"
    assert fake.killed is False, "SIGKILL should not be needed for a cooperative child"


def test_sigkill_escalation_when_bash_does_not_exit(fake_runmgr):
    """If the bash ignores SIGTERM, we MUST escalate to SIGKILL so a
    rogue child cannot hang the driver's finally block.
    """
    from agent import project_loop

    fake = _FakeProc(wait_behavior=[
        KeyboardInterrupt(),
        subprocess.TimeoutExpired(cmd="bash", timeout=10),
        137,  # bash finally exits after SIGKILL
    ])
    with patch.object(project_loop.subprocess, "Popen", return_value=fake):
        with pytest.raises(KeyboardInterrupt):
            project_loop.iterate_projects(
                "run-1", "/tmp/cfg.json", str(fake_runmgr),
            )

    assert fake.terminated is True
    assert fake.killed is True


def test_sigkill_failure_does_not_swallow_keyboard_interrupt(fake_runmgr):
    """Even if SIGKILL fails to reap (e.g. uninterruptible sleep), we
    MUST still re-raise the KeyboardInterrupt so the driver marks the
    run interrupted. We log and move on — RunDriver's finally still
    runs.
    """
    from agent import project_loop

    fake = _FakeProc(wait_behavior=[
        KeyboardInterrupt(),
        subprocess.TimeoutExpired(cmd="bash", timeout=10),  # SIGTERM
        subprocess.TimeoutExpired(cmd="bash", timeout=2),   # SIGKILL
    ])
    with patch.object(project_loop.subprocess, "Popen", return_value=fake):
        with pytest.raises(KeyboardInterrupt):
            project_loop.iterate_projects(
                "run-1", "/tmp/cfg.json", str(fake_runmgr),
            )

    assert fake.terminated is True
    assert fake.killed is True


def test_run_id_override_is_propagated_to_bash_env(fake_runmgr):
    """Regression guard: the dashboard's optimistic-placeholder path
    relies on STATION_RUN_ID_OVERRIDE reaching bash. The signal-
    forwarding refactor MUST NOT drop this env propagation.
    """
    from agent import project_loop

    fake = _FakeProc(wait_behavior=[0])
    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return fake

    with patch.object(project_loop.subprocess, "Popen", side_effect=_fake_popen):
        project_loop.iterate_projects(
            "run-hint-123", "/tmp/cfg.json", str(fake_runmgr),
        )
    assert captured["env"].get("STATION_RUN_ID_OVERRIDE") == "run-hint-123"
