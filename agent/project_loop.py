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
) -> "tuple[int, Any | None, str | None]":
    """Drive a full run in-process: preflight → recovery → per-project → digest.

    Each former bash phase is a Python module; this function composes them
    (issue #383 bash deletion).

    Returns ``(exit_code, last_stream_state, terminal_status_hint)``.

    - ``exit_code``: process-level exit code (0 = clean, non-zero = failure).
    - ``last_stream_state``: the most recent per-project stream state produced
      by :func:`agent.station_orchestrator.orchestrate_project`, or ``None``
      if no project ran successfully far enough to initialise one. RunDriver's
      ``_finalize_telemetry`` consumes it. Threading state via the return
      value (rather than a module global) is correct under any concurrent
      invocation pattern.
    - ``terminal_status_hint``: optional string hint for RunDriver. Currently
      ``"skipped"`` when every enabled project was idle (no eligible issues,
      ``work_attempted=False`` for all) and nothing failed; ``None`` otherwise.
      RunDriver maps ``"skipped"`` to ``status="skipped"`` on run_complete so
      the dashboard can render idle runs distinctly from real failures
      (issues #446 #447).

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
    from agent.station_orchestrator import orchestrate_project, _read_verdicts_file
    from agent.verdict_execution import execute as execute_verdict
    from agent.integration_branch import merge_to_dev, IntegrationBranchError
    from agent.digest import write_digest

    try:
        run_preflight(config_path)
    except PreflightError as exc:
        logger.error("preflight: %s", exc)
        return 2, None, None

    # Queue recovery errors are surfaced (dashboard returning 4xx/5xx is a
    # real problem); transient connect failures already degrade to a no-op
    # inside the recovery functions, so reaching the except branch means
    # something is genuinely wrong.
    try:
        purge_and_recover(run_id)
        resume_paused()
    except QueueRecoveryError as exc:
        logger.error("queue_recovery: %s", exc)
        return 5, None, None

    config = json.loads(Path(config_path).read_text())
    enabled = [p for p in config.get("projects", []) if p.get("enabled", True)]

    results: list[dict] = []
    exit_code = 0
    last_state = None
    log_dir = _os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent")

    # APPROVE_INTEGRATION / APPROVE / PR executors require ``workspace`` and
    # ``dev_branch`` kwargs (kw-only, no default for workspace). ``dev_branch``
    # is global to the run; capture it once.
    dev_branch = config.get("integration", {}).get("dev_branch", "autonomous/dev")

    any_work_attempted = False
    any_real_failure = False

    for project in enabled:
        try:
            workspace_path = ensure_workspace(project, workspaces_dir)
        except WorkspaceError as exc:
            logger.error("workspace: %s", exc)
            exit_code = 3
            any_real_failure = True
            results.append({"project": project["repo"], "decision": "ERROR", "error": str(exc)})
            continue

        # Resolved project mode (defaults to "full" to match the bash picker).
        # Captured once here so both the pre-orchestration plan_review_start
        # emit and the post-verdicts gate call see the same value.
        project_mode = str(project.get("mode") or "full").strip().lower()

        # #442: plan_only projects flip the dashboard banner to
        # ``plan_reviewing`` for the manager-review window. The legacy
        # bash driver emitted this via ``webhook_event plan_review_start``
        # immediately before invoking the manager review step; the Python
        # port (PR #405) dropped it. We re-emit here, right before
        # orchestrate_project (which now hosts the manager as an in-session
        # sibling agent), so the UI state transition is restored.
        if project_mode == "plan_only":
            try:
                from agent.webhook_emitter import emit as _emit_plan_start
                _emit_plan_start(
                    "plan_review_start",
                    run_id=f"run-{run_id}",
                    payload={
                        "project": project.get("repo", ""),
                        "mode": "plan_only",
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort signal
                # Use logger.exception (with traceback) rather than
                # logger.warning so any future signature drift surfaces
                # loudly in logs instead of vanishing into a one-liner.
                # See PR #445 / issue #444 for the manager_no_verdicts
                # variant of this bug.
                logger.exception("plan_review_start webhook emit failed")

        try:
            proj_rc, proj_state, work_attempted = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
            if proj_state is not None:
                last_state = proj_state
            if proj_rc != 0:
                exit_code = proj_rc
                any_real_failure = True
        except (KeyboardInterrupt, OrchestratorStopRequested):
            # Operator stop / SIGTERM-mapped interrupt — propagate so the
            # RunDriver's finally block can write the interrupted-run
            # record. NEVER absorb these into a "project failed" bucket.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrate_project failed for %s", project["repo"])
            exit_code = 4
            any_real_failure = True
            results.append({"project": project["repo"], "decision": "ERROR", "error": str(exc)})
            continue

        if work_attempted:
            any_work_attempted = True

        # #446 / #447: idle case — the project had no eligible issues
        # and the SDK session was never opened. This is NOT a failure;
        # don't read verdicts, don't emit manager_no_verdicts, don't
        # bump exit_code. Emit project_skipped_no_work so the dashboard
        # can render it distinctly from a real failure.
        if not work_attempted:
            try:
                from agent.webhook_emitter import emit as _emit_skip
                _emit_skip(
                    "project_skipped_no_work",
                    run_id=f"run-{run_id}",
                    payload={
                        "project": project.get("repo", ""),
                        "reason": "no_eligible_work",
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort signal
                logger.exception("project_skipped_no_work webhook emit failed")

            results.append({
                "project": project.get("repo", ""),
                "decision": "SKIP",
                "reason": "no_eligible_work",
            })
            continue

        # #390: the manager is now a sibling agent inside the lead's SDK
        # session. It writes verdicts to this path during the run; we just
        # confirm the file exists and hand each verdict to the executor.
        verdicts_path = Path(log_dir) / f"run-{run_id}-verdicts.json"
        verdicts_payload = _read_verdicts_file(verdicts_path)
        if verdicts_payload is None:
            # Missing verdicts is a real failure: the run did work but
            # we have no way to action it. Make this visible — log loudly,
            # emit a webhook so the dashboard can surface it, and bump
            # the per-project exit_code so the run is not falsely
            # marked clean. Operators triaging "why was my issue not
            # touched?" need a visible signal here.
            logger.error(
                "manager sibling produced no verdicts file at %s — "
                "the in-session manager either crashed, hit max-turns, "
                "or the lead never spawned it. All teammate work for "
                "this project is unactioned.",
                verdicts_path,
            )
            try:
                from agent.webhook_emitter import emit as _emit
                _emit(
                    "manager_no_verdicts",
                    run_id=f"run-{run_id}",
                    payload={
                        "project": project.get("repo", ""),
                        "verdicts_path": str(verdicts_path),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort signal
                logger.exception("manager_no_verdicts webhook emit failed")
            exit_code = max(exit_code, 6)
            any_real_failure = True
            results.append({
                "project": project.get("repo", ""),
                "decision": "ERROR",
                "error": f"manager produced no verdicts file at {verdicts_path}",
            })
            # Bail out of this project entirely. There are no verdicts to
            # iterate, no plan-review fan-out to attempt, and no digest to
            # write meaningfully. Falling through to the rest of the loop
            # body is harmless today (raw_verdicts becomes []), but the
            # intent here is "this project failed, move on" — make that
            # explicit so a future downstream phase that assumes
            # verdicts_payload is non-None cannot trip over a half-failed
            # iteration. See PR #445 / issue #444 follow-up.
            continue
        raw_verdicts = (verdicts_payload or {}).get("verdicts", [])

        # #442: plan_only projects fan out into APPROVE_PLAN /
        # REVISE_PLAN / REJECT_PLAN follow-up actions that the manager-sibling
        # writes alongside (or instead of) the regular ``verdicts`` array.
        # The gate parses the same verdicts file, enqueues follow-up full
        # runs, writes revision feedback, and flips the dashboard run
        # status. The legacy bash driver shelled out to
        # ``python -m agent.plan_review_gate``; here we call the function
        # in-process. The CLI entry-point at ``agent.plan_review_gate.main``
        # is preserved for ad-hoc / triage invocations.
        if project_mode == "plan_only":
            try:
                from agent import plan_review_gate as _prg
                _prg.apply_plan_review_gate(
                    project_mode=project_mode,
                    verdicts_path=verdicts_path,
                    project_repo=project.get("repo", ""),
                    run_id=f"run-{run_id}",
                    workspace=workspace_path,
                )
            except Exception:  # noqa: BLE001 — gate is best-effort
                logger.exception(
                    "apply_plan_review_gate failed for %s",
                    project.get("repo", ""),
                )

        from agent.verdict_execution import Verdict as _Verdict
        verdicts = [_Verdict.from_dict(v) for v in raw_verdicts]

        for verdict in verdicts:
            # APPROVE_INTEGRATION / APPROVE / PR executors require
            # ``workspace`` (kw-only, no default) and use ``dev_branch``
            # for non-degraded behavior. Missing kwargs raise TypeError
            # that propagates out and gets caught at the RunDriver level,
            # silently failing every integration even when the manager
            # approved. (Discovered after live run-20260515T235612Z:
            # verdict file written with 5x APPROVE_INTEGRATION, no PRs
            # opened, run marked failed.) Catch per-verdict so one bad
            # executor invocation doesn't cancel the whole queue.
            try:
                execute_verdict(
                    verdict,
                    workspace=Path(workspace_path),
                    run_id=run_id,
                    env=_os.environ.copy(),
                    dev_branch=dev_branch,
                )
            except Exception as exc:  # noqa: BLE001 — must not crash loop
                logger.exception(
                    "verdict_execution: %s#%s failed (%s)",
                    verdict.project, verdict.issue_number, verdict.verdict,
                )
                exit_code = max(exit_code, 7)
                any_real_failure = True
                results.append({
                    "project": verdict.project,
                    "issue_number": verdict.issue_number,
                    "decision": "ERROR",
                    "error": f"execute_verdict: {type(exc).__name__}: {exc}",
                })
                continue
            results.append({
                "project": verdict.project,
                "issue_number": verdict.issue_number,
                "decision": verdict.verdict,
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

    # #446 / #447: idle-run terminal status hint for RunDriver.
    # Only emit "skipped" if EVERY enabled project was idle AND
    # nothing genuinely failed. Conservative: anything ambiguous
    # falls through to existing completed/failed mapping in RunDriver.run().
    # Note: an empty ``enabled`` list (no enabled projects at all) is a
    # misconfiguration, not an idle run; fall through to "completed" so
    # the operator sees the run completed rather than masking the config
    # gap behind a "skipped" status.
    if enabled and not any_work_attempted and not any_real_failure and exit_code == 0:
        terminal_status_hint = "skipped"
    else:
        terminal_status_hint = None
    return exit_code, last_state, terminal_status_hint
