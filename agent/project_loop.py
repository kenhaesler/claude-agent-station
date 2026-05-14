"""Per-project iteration entry-point.

Drives a full run in-process: preflight → queue recovery → per-project
(workspace setup → orchestrate_project → manager review → verdict
execution → optional merge-to-dev) → digest.

Each former bash phase is a dedicated Python module under ``agent/``
(issue #383).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)



# Labels that exclude an issue from the analyzable set. Mirrors the
# These labels are kept in sync with the bash picker that was removed in
# issue #383. They mirror the former inline SKIP set in get_analyzable_issues.
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


def iterate_projects(
    run_id: str, config_path: str, workspaces_dir: str,
) -> tuple[int, "Any | None"]:
    """Drive a full run in-process: preflight → recovery → per-project → digest.

    Each former bash phase is a Python module; this function composes them
    (issue #383 bash deletion).

    Returns ``(exit_code, last_stream_state)``. ``last_stream_state`` is the
    most recent per-project stream state produced by
    :func:`agent.station_orchestrator.orchestrate_project`, or ``None`` if
    no project ran successfully far enough to initialise one. RunDriver's
    ``_finalize_telemetry`` consumes it. Threading state via the return
    value (rather than a module global) is correct under any concurrent
    invocation pattern.

    Exception policy: ``OrchestratorStopRequested`` and
    ``KeyboardInterrupt`` always propagate up so the driver's finally
    block can mark the run interrupted. Other exceptions per-project are
    caught, logged, and counted as project failures.
    """
    import asyncio
    import json
    import os as _os

    from agent.preflight import run_preflight, PreflightError
    from agent.queue_recovery import (
        purge_and_recover, resume_paused, QueueRecoveryError,
    )
    from agent.run_control import OrchestratorStopRequested
    from agent.workspace_setup import ensure_workspace, WorkspaceError
    from agent.station_orchestrator import orchestrate_project
    from agent.manager_review import run_manager_review, ManagerReviewError
    from agent.verdict_execution import execute as execute_verdict
    from agent.integration_branch import merge_to_dev, IntegrationBranchError
    from agent.digest import write_digest

    try:
        run_preflight(config_path)
    except PreflightError as exc:
        logger.error("preflight: %s", exc)
        return 2, None

    # Queue recovery errors are surfaced (dashboard returning 4xx/5xx is a
    # real problem); transient connect failures already degrade to a no-op
    # inside the recovery functions, so reaching the except branch means
    # something is genuinely wrong.
    try:
        purge_and_recover(run_id)
        resume_paused()
    except QueueRecoveryError as exc:
        logger.error("queue_recovery: %s", exc)
        return 5, None

    config = json.loads(Path(config_path).read_text())
    enabled = [p for p in config.get("projects", []) if p.get("enabled", True)]

    results: list[dict] = []
    exit_code = 0
    last_state = None
    log_dir = _os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent")

    for project in enabled:
        try:
            ensure_workspace(project, workspaces_dir)
        except WorkspaceError as exc:
            logger.error("workspace: %s", exc)
            exit_code = 3
            results.append({"project": project["name"], "decision": "ERROR", "error": str(exc)})
            continue

        try:
            proj_rc, proj_state = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
            if proj_state is not None:
                last_state = proj_state
            if proj_rc != 0:
                exit_code = proj_rc
        except (KeyboardInterrupt, OrchestratorStopRequested):
            # Operator stop / SIGTERM-mapped interrupt — propagate so the
            # RunDriver's finally block can write the interrupted-run
            # record. NEVER absorb these into a "project failed" bucket.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrate_project failed for %s", project["name"])
            exit_code = 4
            results.append({"project": project["name"], "decision": "ERROR", "error": str(exc)})
            continue

        # Manager review consumes the review package the orchestrator wrote.
        pkg = Path(log_dir) / f"{run_id}-{project['name'].replace('/', '__')}-review.md"
        try:
            verdicts = run_manager_review(str(pkg), run_id, config)
        except ManagerReviewError as exc:
            logger.warning("manager_review: %s", exc)
            verdicts = []

        for verdict in verdicts:
            execute_verdict(verdict, run_id=run_id)
            results.append({
                "project": verdict.project,
                "issue_number": verdict.issue_number,
                "decision": verdict.decision,
                "branch": getattr(verdict, "branch", ""),
                "reasoning": getattr(verdict, "reasoning", ""),
            })
            if getattr(verdict, "action", "") == "merge_dev":
                try:
                    merge_to_dev(
                        project=verdict.project,
                        feature_branch=verdict.branch,
                        base_branch=verdict.base_branch,
                        issue_number=verdict.issue_number,
                        reasoning=verdict.reasoning or "",
                        workspaces_dir=workspaces_dir,
                    )
                except IntegrationBranchError as exc:
                    logger.error("merge_to_dev failed: %s", exc)
                    results.append({
                        "project": verdict.project,
                        "issue_number": verdict.issue_number,
                        "decision": "ERROR",
                        "error": f"merge_to_dev: {exc}",
                    })

    write_digest(run_id=run_id, results=results, log_dir=log_dir)
    return exit_code, last_state
