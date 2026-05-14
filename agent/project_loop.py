"""Per-project iteration entry-point.

Currently delegates to the existing bash body for the orchestration
flow (workspace setup → orchestrator → manager review → verdicts) via
``run-manager.sh --internal-iterate``. The Python side of the migration
is being built up incrementally:

- M1 (#349) introduced this module as a shim and built RunDriver.
- M2/#361 wired RunDriver as the launcher's default entry point and
  closed the webhook-payload parity gap. Bash now writes a telemetry
  JSON dump so the Python driver can ship ``run_complete`` correctly.
- M2/#362 (this module) adds :func:`pick_issue` — the Python-native
  equivalent of the bash's ``get_analyzable_issues`` label filter,
  exposed here so future callers don't have to shell to bash.
- M2/#363 ships ``agent/verdict_execution.py`` for the gh/git side.

The full bash deletion (in-process orchestrator dispatch, deletion of
the workspace-setup / manager-review / verdict-execution blocks) is
deferred to follow-up sessions: it requires moving four separate bash
phases out of ``run-manager.sh`` and would not fit alongside the M2
foundation work.

See spec/plan: 2026-05-11-run-lifecycle-overhaul.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level slot for the last stream state produced by iterate_projects.
# RunDriver._finalize_telemetry reads this after the run to copy counters
# in-process (replaces the bash telemetry JSON hand-off, issue #383).
_LAST_STREAM_STATE = None


def get_last_stream_state():
    """Return the last _StreamState set during iterate_projects, or None."""
    return _LAST_STREAM_STATE


def _set_last_stream_state(state) -> None:
    global _LAST_STREAM_STATE
    _LAST_STREAM_STATE = state


# How long to wait for the bash child to exit on SIGTERM before
# escalating to SIGKILL. The bash's EXIT trap writes the telemetry
# dump RunDriver reads, so we want to give it enough time to flush.
_BASH_SIGTERM_GRACE_SECONDS = 10
_BASH_SIGKILL_GRACE_SECONDS = 2


# Labels that exclude an issue from the analyzable set. Mirrors the
# inline SKIP set in ``agent/scripts/run-manager.sh::get_analyzable_issues``
# (~line 889). Kept in lock-step deliberately so the bash and Python
# pickers select identically until the bash version is deleted.
SKIP_LABELS: frozenset[str] = frozenset({
    "autonomous-agent/refined",
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "NO AI",
    "backlog",
    "wontfix",
})


@dataclass
class IssueDesc:
    """Minimal projection of a GitHub issue used by the project loop.

    Keeps the surface tiny so tests can construct fixtures without
    a full gh-shaped payload.
    """

    number: int
    title: str
    labels: tuple[str, ...]


def _eligible(issue: dict[str, Any],
              extra_skip: frozenset[str] | None = None) -> bool:
    """Return True iff this gh-issue dict is eligible for the project
    loop. Bash parity rule: exclude issues that carry any skip-label.
    """
    label_names = {label.get("name", "") for label in issue.get("labels") or []}
    skip = SKIP_LABELS | (extra_skip or frozenset())
    return not (label_names & skip)


def pick_issue(
    repo: str,
    *,
    extra_skip: frozenset[str] | None = None,
    env: dict[str, str] | None = None,
    limit: int = 100,
) -> IssueDesc | None:
    """Pick the next eligible open issue for ``repo``.

    Selection rule: oldest by issue number among issues that do not
    carry any label in :data:`SKIP_LABELS` (plus any per-call
    ``extra_skip`` the caller supplies). Returns ``None`` when no
    issue is eligible.

    This mirrors the bash ``get_analyzable_issues`` filter (#362). It
    does NOT yet replace the bash's downstream selection inside
    ``assign_work`` (which also runs a Haiku-driven assigner agent
    for multi-employee projects) — that step stays in bash until the
    full project-loop port lands in a follow-up session.

    Raises :class:`agent.gh_client.GhError` if the gh subprocess fails,
    or :class:`json.JSONDecodeError` if gh returned non-JSON output.
    """
    from agent.gh_client import gh_json

    issues = gh_json(
        [
            "issue", "list",
            "--repo", repo,
            "--state", "open",
            "--limit", str(limit),
            "--json", "number,title,labels",
        ],
        env=env,
    ) or []

    eligible = [i for i in issues if _eligible(i, extra_skip)]
    if not eligible:
        return None

    # Selection rule: lowest issue number first. Note this is NEW
    # selection logic — the bash ``get_analyzable_issues`` returns the
    # full filtered array and lets downstream code (assign_work) decide.
    # Our follow-up wiring uses this rule for single-employee runs;
    # multi-employee assignment continues to delegate to the bash's
    # Haiku-driven assigner.
    chosen = min(eligible, key=lambda i: int(i["number"]))
    return IssueDesc(
        number=int(chosen["number"]),
        title=str(chosen.get("title") or ""),
        labels=tuple(str(l.get("name", "")) for l in chosen.get("labels") or []),
    )


def iterate_projects(run_id: str, config_path: str,
                     workspaces_dir: str) -> int:
    """Iterate over enabled projects and dispatch agent work.

    Currently shells to ``run-manager.sh --internal-iterate`` which
    contains the legacy bash body. The follow-up port replaces this
    with native Python that calls :func:`pick_issue` per project and
    then invokes the orchestrator in-process. That migration is
    deferred (see module docstring) because it also requires porting
    workspace setup, queue handling, manager review, and verdict
    execution out of bash.

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
