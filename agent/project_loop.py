"""Per-project iteration entry-point.

This module currently delegates to the existing bash logic via
subprocess. As the migration progresses (follow-up sub-PRs to #349),
the per-project body will be ported here directly. The interface is
stable across the migration so the RunDriver doesn't change.

See spec/plan: 2026-05-11-run-lifecycle-overhaul.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# How long to wait for the bash child to exit on SIGTERM before
# escalating to SIGKILL. The bash's EXIT trap writes the telemetry
# dump RunDriver reads, so we want to give it enough time to flush.
_BASH_SIGTERM_GRACE_SECONDS = 10
_BASH_SIGKILL_GRACE_SECONDS = 2


def iterate_projects(run_id: str, config_path: str,
                     workspaces_dir: str) -> int:
    """Iterate over enabled projects and dispatch agent work.

    Currently shells to ``run-manager.sh --internal-iterate`` which
    contains the legacy bash body. Migration sub-PRs will replace this
    with native Python iteration.

    Signal handling (#361 fix): if the calling Python process (the
    RunDriver) is interrupted (SIGINT raises ``KeyboardInterrupt``
    directly; SIGTERM is mapped to ``KeyboardInterrupt`` by the driver
    so it flows through this same path), we forward the signal to the
    bash subprocess and wait for it to finish so its EXIT trap can
    write its telemetry dump. Without this forwarding the bash would
    keep running, orphaned, after Python had already emitted
    ``run_complete``.

    Returns exit code: 0 on success, non-zero on failure. Re-raises
    ``KeyboardInterrupt`` after the bash child has exited so the
    caller's ``except KeyboardInterrupt`` branch can mark the run
    ``interrupted``.
    """
    script_dir = Path(__file__).resolve().parent / "scripts"
    runmgr = script_dir / "run-manager.sh"
    if not runmgr.exists():
        logger.error("project_loop: run-manager.sh not found at %s", runmgr)
        return 127

    env = os.environ.copy()
    env["STATION_RUN_ID_OVERRIDE"] = run_id

    logger.info("project_loop: shelling to %s --internal-iterate", runmgr)
    proc = subprocess.Popen(
        [str(runmgr), "--internal-iterate"],
        env=env,
        cwd=workspaces_dir if Path(workspaces_dir).exists() else None,
    )
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Forward the signal to the bash child so its EXIT trap fires
        # (the bash trap is what writes the telemetry dump RunDriver
        # reads). Without this the bash continues running after the
        # Python driver has already marked the run interrupted.
        logger.warning(
            "iterate_projects: interrupted — forwarding SIGTERM to bash pid=%s",
            proc.pid,
        )
        _terminate_child(proc)
        raise


def _terminate_child(proc: subprocess.Popen) -> None:
    """SIGTERM → wait → SIGKILL ladder. Bounded by the two grace
    constants so an unresponsive child can't hang the driver.
    """
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=_BASH_SIGTERM_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        logger.warning(
            "iterate_projects: bash pid=%s did not exit in %ss; SIGKILL",
            proc.pid, _BASH_SIGTERM_GRACE_SECONDS,
        )
    proc.kill()
    try:
        proc.wait(timeout=_BASH_SIGKILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # The OS should have killed the process by now; if we still
        # can't reap it, log and move on so the driver's finally
        # block runs.
        logger.error(
            "iterate_projects: bash pid=%s did not exit after SIGKILL",
            proc.pid,
        )
