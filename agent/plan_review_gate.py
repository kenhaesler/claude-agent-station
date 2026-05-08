"""Plan review gate — pre-implementation enforcement for ``plan_only`` mode.

Issue #266 introduces a four-state project mode. The ``plan_only`` mode
inserts a manual gate between plan-writing and implementation:

    teammate writes plan → manager reviews plan →
        APPROVE_PLAN  → enqueue follow-up ``full`` run referencing the plan
        REVISE_PLAN   → re-spawn the same teammate with feedback (bounded
                        by STATION_PLAN_REVISION_MAX, default 2)
        REJECT_PLAN   → close the issue path with a comment, do not
                        implement

This module owns the post-run gate logic: parsing the manager's plan
verdict, enqueuing follow-up work, and emitting the corresponding run
state transitions. The actual re-spawn loop for REVISE_PLAN is currently
implemented as a documented helper that the run-manager shell driver
calls between iterations — wiring it into the live SDK session is
deliberately out-of-scope for the initial cut (see TODO in
``apply_plan_verdict``).

States added to the run / queue lifecycle (additive — both columns are
TEXT in SQLite, so old code accepts the new strings without a
migration):

- ``awaiting_plan_review`` — plan_only run finished, manager verdict pending
- ``plan_approved``        — APPROVE_PLAN; follow-up ``full`` run enqueued
- ``plan_rejected``        — REJECT_PLAN; no follow-up

The follow-up ``full`` run carries an ``approved_plan_path`` in its
``QueueItem.context`` so the implementing teammate reads the approved
plan as guidance (see ``employee.md`` ``APPROVED_PLAN`` block).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)


# Lifecycle states (issue #266). Additive — old code keeps working.
RUN_STATE_AWAITING_PLAN_REVIEW = "awaiting_plan_review"
RUN_STATE_PLAN_APPROVED = "plan_approved"
RUN_STATE_PLAN_REJECTED = "plan_rejected"

PLAN_REVIEW_RUN_STATES = (
    RUN_STATE_AWAITING_PLAN_REVIEW,
    RUN_STATE_PLAN_APPROVED,
    RUN_STATE_PLAN_REJECTED,
)


# Default max revisions per plan_only run before we give up. Override via
# STATION_PLAN_REVISION_MAX env var. Kept as a module constant so tests can
# patch it cheaply; the env-var read happens in ``get_plan_revision_max()``
# so changes apply at call time, not import time.
DEFAULT_PLAN_REVISION_MAX = 2


def get_plan_revision_max() -> int:
    """Return the max plan-revision iteration count.

    Reads ``STATION_PLAN_REVISION_MAX`` at call time (so tests can monkey-
    patch the environment), falling back to ``DEFAULT_PLAN_REVISION_MAX``.
    Negative or non-integer values fall back to the default.
    """
    raw = os.environ.get("STATION_PLAN_REVISION_MAX")
    if not raw:
        return DEFAULT_PLAN_REVISION_MAX
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "STATION_PLAN_REVISION_MAX=%r is not an integer; using default %d",
            raw, DEFAULT_PLAN_REVISION_MAX,
        )
        return DEFAULT_PLAN_REVISION_MAX
    return n if n >= 0 else DEFAULT_PLAN_REVISION_MAX


@dataclass(frozen=True)
class PlanVerdict:
    """A single manager verdict on a plan_only employee plan."""
    verdict: Literal["APPROVE_PLAN", "REVISE_PLAN", "REJECT_PLAN"]
    employee_index: int
    issue_number: int | None
    plan_path: str | None  # Absolute path to .claude-employee-plan-{index}.json
    feedback: str  # Required for REVISE_PLAN; informational otherwise
    plan_quality_score: int | None = None


@dataclass(frozen=True)
class GateAction:
    """The orchestrator's resolved next-step after a plan verdict.

    ``kind`` decides downstream behavior:

    - ``"enqueue_full_run"``   — build a follow-up run with mode="full" and
                                 ``approved_plan_path`` in the queue item
                                 context. ``next_run_state`` is
                                 ``plan_approved``.
    - ``"revise"``             — re-spawn the same teammate with the manager
                                 feedback + prior plan path. The orchestrator
                                 / run-manager shim drives the loop, bounded
                                 by ``get_plan_revision_max()``.
    - ``"reject"``             — close the planning thread, no follow-up.
                                 ``next_run_state`` is ``plan_rejected``.
    - ``"halt_revisions_exhausted"`` — REVISE_PLAN was returned but the
                                 revision counter has hit the cap. Treated
                                 as a soft REJECT.
    """
    kind: Literal[
        "enqueue_full_run",
        "revise",
        "reject",
        "halt_revisions_exhausted",
    ]
    verdict: PlanVerdict
    next_run_state: str
    follow_up_context: dict | None = None  # for enqueue_full_run / revise


def parse_plan_verdicts(verdicts_path: str | os.PathLike) -> list[PlanVerdict]:
    """Read a manager plan-verdicts JSON file and return parsed entries.

    File layout matches ``REPORT-SCHEMAS.md`` "Manager Plan Verdict":

    .. code-block:: json

        {
          "plan_verdicts": [
            {"verdict": "APPROVE_PLAN", "employee_index": 0,
             "issue_number": 42, "feedback": "..."}
          ]
        }

    Returns an empty list if the file is missing, unreadable, or contains
    no ``plan_verdicts`` key — never raises. Bad rows are skipped with a
    warning rather than aborting the whole batch.
    """
    p = Path(verdicts_path)
    if not p.is_file():
        logger.info("Plan verdicts file not found: %s", p)
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse plan verdicts file %s: %s", p, exc)
        return []

    rows = data.get("plan_verdicts") or []
    if not isinstance(rows, list):
        return []

    out: list[PlanVerdict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        v = raw.get("verdict")
        if v not in ("APPROVE_PLAN", "REVISE_PLAN", "REJECT_PLAN"):
            logger.warning("Skipping plan verdict with unknown verdict %r", v)
            continue
        try:
            out.append(PlanVerdict(
                verdict=v,
                employee_index=int(raw.get("employee_index", 0)),
                issue_number=raw.get("issue_number"),
                plan_path=raw.get("plan_path") or raw.get("approved_plan_path"),
                feedback=str(raw.get("feedback") or ""),
                plan_quality_score=raw.get("plan_quality_score"),
            ))
        except (TypeError, ValueError) as exc:
            logger.warning("Malformed plan verdict %r: %s", raw, exc)
    return out


def apply_plan_verdict(
    verdict: PlanVerdict,
    *,
    project_repo: str,
    revision_count: int = 0,
) -> GateAction:
    """Resolve a single plan verdict into the next gate action.

    The caller (orchestrator post-run hook OR run-manager shim) is
    responsible for actually performing the side-effects implied by the
    returned ``GateAction``:

    - ``enqueue_full_run``  → POST a new ``QueueItem`` with mode="full",
                              context={"approved_plan_path": ...}, and
                              parent run linkage.
    - ``revise``            → re-spawn the same teammate; pass the manager
                              feedback as ``plan_revision_feedback`` to
                              :func:`build_mode_block`.
    - ``reject`` /
      ``halt_revisions_exhausted`` → close the issue path with a comment.

    REVISE_PLAN past ``get_plan_revision_max()`` is downgraded to
    ``halt_revisions_exhausted`` — we treat exhausted-revisions as a soft
    rejection, matching the issue body's "loop bounded by config".

    TODO(#266 follow-up): wire ``revise`` into the live SDK session. The
    current iteration implements the verdict mapping + tests; the actual
    re-spawn-with-feedback loop is driven by the run-manager shell driver
    between iterations.
    """
    if verdict.verdict == "APPROVE_PLAN":
        return GateAction(
            kind="enqueue_full_run",
            verdict=verdict,
            next_run_state=RUN_STATE_PLAN_APPROVED,
            follow_up_context={
                "project_repo": project_repo,
                "issue_number": verdict.issue_number,
                "approved_plan_path": verdict.plan_path,
                "mode": "full",
                "parent_employee_index": verdict.employee_index,
            },
        )

    if verdict.verdict == "REVISE_PLAN":
        if revision_count >= get_plan_revision_max():
            return GateAction(
                kind="halt_revisions_exhausted",
                verdict=verdict,
                next_run_state=RUN_STATE_PLAN_REJECTED,
            )
        return GateAction(
            kind="revise",
            verdict=verdict,
            next_run_state=RUN_STATE_AWAITING_PLAN_REVIEW,
            follow_up_context={
                "project_repo": project_repo,
                "issue_number": verdict.issue_number,
                "prior_plan_path": verdict.plan_path,
                "plan_revision_feedback": verdict.feedback,
                "revision_count": revision_count + 1,
            },
        )

    # REJECT_PLAN
    return GateAction(
        kind="reject",
        verdict=verdict,
        next_run_state=RUN_STATE_PLAN_REJECTED,
    )


def build_followup_queue_item(action: GateAction) -> dict:
    """Build the ``QueueItem`` payload for an ``enqueue_full_run`` action.

    Returns a dict matching the QueueCreate Pydantic schema:

    - ``project_repo`` from the gate context
    - ``issue_number`` from the original plan_only run
    - ``mode="full"``
    - ``state="pending"`` so the next run picks it up
    - ``context`` carries the approved plan path so the implementing
      teammate can read it as ``APPROVED_PLAN`` (per employee.md).
    """
    if action.kind != "enqueue_full_run":
        raise ValueError(
            f"build_followup_queue_item: expected enqueue_full_run action, "
            f"got {action.kind}"
        )
    ctx = action.follow_up_context or {}
    return {
        "project_repo": ctx.get("project_repo", ""),
        "issue_number": ctx.get("issue_number"),
        "mode": "full",
        "state": "pending",
        "context": json.dumps({
            "approved_plan_path": ctx.get("approved_plan_path"),
            "from_plan_only_run": True,
            "parent_employee_index": ctx.get("parent_employee_index"),
        }),
    }


# ── Live wiring ───────────────────────────────────────────────────────────
#
# The pure helpers above are unit-testable in isolation. Everything below
# wires them into the live system:
#
# - :func:`post_queue_item` POSTs to ``/api/queue`` with auth.
# - :func:`post_run_event` POSTs to ``/api/webhook/run-event`` to flip the
#   run's status to ``awaiting_plan_review``, ``plan_approved``, or
#   ``plan_rejected`` (handlers added in run_lifecycle.py).
# - :func:`apply_plan_review_gate` is the orchestrator-side entry point:
#   parse → decide → execute. Called by :func:`main` (CLI) which is in
#   turn invoked from :file:`agent/scripts/run-manager.sh` after the
#   manager review phase, gated on ``project.mode == "plan_only"``.
#
# Network failures are logged and converted to non-zero exits so the
# shell driver can surface them, but they never raise — the gate is a
# side-effect layer, not a fail-stop in the main flow.


def _resolve_dashboard_url(api_url: str | None = None) -> str:
    """Resolve the dashboard base URL.

    Precedence: explicit arg → ``STATION_DASHBOARD_URL`` env →
    ``STATION_WEBHOOK_URL`` env (stripped of the ``/api/webhook/...``
    suffix) → ``http://127.0.0.1:8420``. Matches ``run-manager.sh``
    queue_api/webhook_event resolution so a single deployment env var
    works for both layers.
    """
    if api_url:
        return api_url.rstrip("/")
    env = os.environ.get("STATION_DASHBOARD_URL", "").strip()
    if env:
        return env.rstrip("/")
    wh = os.environ.get("STATION_WEBHOOK_URL", "").strip()
    if wh:
        # Strip a trailing /api/webhook/... path component to recover the
        # base URL.
        for suffix in ("/api/webhook/run-event", "/api/webhook"):
            if wh.endswith(suffix):
                return wh[: -len(suffix)].rstrip("/")
        return wh.rstrip("/")
    return "http://127.0.0.1:8420"


def _auth_headers() -> dict[str, str]:
    """Return Bearer-auth headers when ``STATION_API_KEY`` is set.

    Mirrors the dashboard's :func:`verify_api_key` dep — open-by-default
    when the key is empty so unauth'd local dev keeps working.
    """
    key = os.environ.get("STATION_API_KEY", "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _webhook_auth_headers() -> dict[str, str]:
    """Return webhook auth header when ``STATION_WEBHOOK_SECRET`` is set."""
    secret = os.environ.get("STATION_WEBHOOK_SECRET", "").strip()
    if not secret:
        return {}
    return {"X-Webhook-Token": secret}


def post_queue_item(
    payload: dict[str, Any],
    *,
    api_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """POST a queue-item payload to the dashboard's ``/api/queue`` endpoint.

    Returns the parsed response on success (the new queue item) or
    ``None`` on any error. Best-effort — logs and swallows network errors
    so the gate driver can finish even if the dashboard is unreachable.

    The ``payload`` argument should already match :class:`QueueItemCreate`
    (typically built by :func:`build_followup_queue_item`).
    """
    base = _resolve_dashboard_url(api_url)
    url = f"{base}/api/queue"
    headers = {"Content-Type": "application/json", **_auth_headers()}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except ValueError:
                logger.warning("Queue POST returned non-JSON: %s", resp.text[:200])
                return None
        logger.warning(
            "Queue POST %s returned %d: %s",
            url, resp.status_code, resp.text[:200],
        )
    except httpx.RequestError as exc:
        logger.warning("Queue POST %s failed: %s", url, exc)
    return None


def post_run_event(
    event: str,
    run_id: str,
    *,
    extra: dict[str, Any] | None = None,
    api_url: str | None = None,
    timeout: float = 3.0,
) -> bool:
    """POST a webhook ``run-event`` to flip a Run's status.

    Used by the gate to drive ``awaiting_plan_review`` →
    ``plan_approved`` / ``plan_rejected`` transitions on the Run row.
    The handlers live in :mod:`app.services.run_lifecycle`.

    Returns ``True`` on 2xx, ``False`` otherwise. Network failures are
    logged but never raise — the gate side-effects are best-effort by
    design.
    """
    base = _resolve_dashboard_url(api_url)
    url = f"{base}/api/webhook/run-event"
    headers = {"Content-Type": "application/json", **_webhook_auth_headers()}
    body: dict[str, Any] = {"event": event, "run_id": run_id}
    if extra:
        body.update(extra)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "run-event %s for %s returned %d: %s",
            event, run_id, resp.status_code, resp.text[:200],
        )
    except httpx.RequestError as exc:
        logger.warning("run-event %s for %s failed: %s", event, run_id, exc)
    return False


def write_revision_feedback(
    workspace: str | os.PathLike,
    employee_index: int,
    feedback: str,
    *,
    revision_count: int,
    prior_plan_path: str | None,
) -> str:
    """Write REVISE_PLAN feedback to a workspace file the next teammate
    re-spawn can pick up.

    The actual live re-spawn loop is deferred (see TODO in
    :func:`apply_plan_verdict`); this helper at least preserves the
    feedback durably so a follow-up run can find it. Returns the path
    written.
    """
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / f".claude-plan-revision-feedback-{employee_index}.json"
    payload = {
        "employee_index": employee_index,
        "revision_count": revision_count,
        "prior_plan_path": prior_plan_path,
        "feedback": feedback,
    }
    path.write_text(json.dumps(payload, indent=2))
    return str(path)


@dataclass(frozen=True)
class GateOutcome:
    """Aggregated result of running the gate over one verdict.

    Returned to the CLI / shell driver so it can log per-verdict outcomes
    and decide whether the gate as a whole succeeded.
    """
    action_kind: str
    verdict_kind: str
    issue_number: int | None
    queue_item_id: int | None  # populated on enqueue_full_run success
    feedback_path: str | None  # populated on revise
    next_run_state: str
    posted_status_event: bool


def apply_plan_review_gate(
    project_mode: str,
    verdicts_path: str | os.PathLike,
    project_repo: str,
    run_id: str,
    *,
    workspace: str | os.PathLike | None = None,
    revision_count: int = 0,
    api_url: str | None = None,
) -> list[GateOutcome]:
    """Drive the plan-review gate for one project's manager-review output.

    This is the orchestrator-side entry point. It:

    1. Returns immediately with an empty list when ``project_mode`` is not
       ``"plan_only"`` — every other mode skips the gate entirely.
    2. Posts an ``awaiting_plan_review`` run event so the dashboard banner
       reflects the gate before any side-effects fire.
    3. Reads the manager's plan verdicts via :func:`parse_plan_verdicts`.
    4. For each verdict, decides via :func:`apply_plan_verdict`, executes
       the resulting :class:`GateAction`, and posts a follow-up run event
       to flip the Run row to ``plan_approved`` / ``plan_rejected``.

    A REVISE_PLAN within budget keeps the run in ``awaiting_plan_review``
    and writes the feedback to a workspace file via
    :func:`write_revision_feedback`. The actual live re-spawn loop is a
    documented TODO — wiring it into the SDK session is intentionally
    out-of-scope for the initial cut (see issue #266 deliverable
    summary).
    """
    if project_mode != "plan_only":
        logger.debug("Gate skipped: project_mode=%s is not plan_only", project_mode)
        return []

    # Mark the run as awaiting plan review BEFORE applying any verdicts so
    # operators see the gate engage even if the verdicts file is malformed
    # or the manager produced nothing. The ``plan_reviewing`` window is
    # the manager-review phase itself (handled by run-manager.sh emitting
    # plan_review_start for plan_only projects); ``awaiting_plan_review``
    # is the post-review window where the gate is applying verdicts.
    post_run_event(
        "awaiting_plan_review",
        run_id,
        extra={"status": "awaiting_plan_review", "project": project_repo, "mode": project_mode},
        api_url=api_url,
    )

    verdicts = parse_plan_verdicts(verdicts_path)
    if not verdicts:
        logger.warning(
            "Gate: no plan verdicts found at %s for run %s; leaving status as "
            "awaiting_plan_review for manual resolution.",
            verdicts_path, run_id,
        )
        return []

    outcomes: list[GateOutcome] = []
    any_approved = False
    any_active_revision = False

    for verdict in verdicts:
        action = apply_plan_verdict(
            verdict,
            project_repo=project_repo,
            revision_count=revision_count,
        )
        queue_item_id: int | None = None
        feedback_path: str | None = None
        posted = False

        if action.kind == "enqueue_full_run":
            payload = build_followup_queue_item(action)
            resp = post_queue_item(payload, api_url=api_url)
            if resp:
                queue_item_id = resp.get("id")
                logger.info(
                    "Gate: APPROVE_PLAN issue #%s → enqueued follow-up full run "
                    "(queue_item_id=%s, plan=%s)",
                    verdict.issue_number, queue_item_id,
                    (action.follow_up_context or {}).get("approved_plan_path"),
                )
                any_approved = True
            else:
                logger.warning(
                    "Gate: APPROVE_PLAN for issue #%s — queue POST failed; "
                    "follow-up run NOT enqueued.",
                    verdict.issue_number,
                )
                # If the enqueue fails, the gate should NOT mark the run as
                # plan_approved — that would lose the work. Leave it in
                # awaiting_plan_review for operator inspection.
                outcomes.append(GateOutcome(
                    action_kind=action.kind,
                    verdict_kind=verdict.verdict,
                    issue_number=verdict.issue_number,
                    queue_item_id=None,
                    feedback_path=None,
                    next_run_state=RUN_STATE_AWAITING_PLAN_REVIEW,
                    posted_status_event=False,
                ))
                continue

        elif action.kind == "revise":
            if workspace is not None:
                feedback_path = write_revision_feedback(
                    workspace,
                    verdict.employee_index,
                    verdict.feedback,
                    revision_count=(action.follow_up_context or {}).get(
                        "revision_count", revision_count + 1,
                    ),
                    prior_plan_path=verdict.plan_path,
                )
            logger.info(
                "Gate: REVISE_PLAN issue #%s (revision %s/%s) — feedback at %s. "
                "TODO: wire live re-spawn loop into the SDK session.",
                verdict.issue_number,
                (action.follow_up_context or {}).get("revision_count", "?"),
                get_plan_revision_max(),
                feedback_path or "<workspace not provided>",
            )
            any_active_revision = True

        elif action.kind == "halt_revisions_exhausted":
            logger.info(
                "Gate: REVISE_PLAN exhausted (%s/%s) for issue #%s — treating as "
                "REJECT_PLAN.",
                revision_count, get_plan_revision_max(), verdict.issue_number,
            )

        else:  # reject
            logger.info(
                "Gate: REJECT_PLAN issue #%s — no follow-up enqueued. Reasoning: %s",
                verdict.issue_number, (verdict.feedback or "")[:200],
            )

        outcomes.append(GateOutcome(
            action_kind=action.kind,
            verdict_kind=verdict.verdict,
            issue_number=verdict.issue_number,
            queue_item_id=queue_item_id,
            feedback_path=feedback_path,
            next_run_state=action.next_run_state,
            posted_status_event=False,
        ))

    # Decide the run-level status transition. If ANY verdict approved a
    # plan and the enqueue succeeded, the run is plan_approved. Otherwise,
    # if ANY revision is in flight, leave the run in awaiting_plan_review.
    # Otherwise (all rejects / exhausted), mark the run as plan_rejected.
    if any_approved:
        terminal_event = "plan_approved"
    elif any_active_revision:
        terminal_event = "awaiting_plan_review"
    else:
        terminal_event = "plan_rejected"

    posted_terminal = post_run_event(
        terminal_event,
        run_id,
        extra={
            "status": terminal_event,
            "project": project_repo,
            "mode": project_mode,
        },
        api_url=api_url,
    )
    # Backfill the posted flag on the most recent outcome so callers can
    # see that the terminal status was sent. We only emit one per gate
    # invocation (multiple verdicts collapse to a single run state).
    if outcomes:
        last = outcomes[-1]
        outcomes[-1] = GateOutcome(
            action_kind=last.action_kind,
            verdict_kind=last.verdict_kind,
            issue_number=last.issue_number,
            queue_item_id=last.queue_item_id,
            feedback_path=last.feedback_path,
            next_run_state=last.next_run_state,
            posted_status_event=posted_terminal,
        )

    return outcomes


# ── CLI entry point ─────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agent.plan_review_gate",
        description=(
            "Plan-review gate driver. Invoked by run-manager.sh after the "
            "manager review phase for plan_only projects. Reads the manager's "
            "plan-verdicts JSON, decides per-verdict, and executes the "
            "follow-up actions (enqueue full run, write revision feedback, "
            "or close the planning thread)."
        ),
    )
    p.add_argument("--project-mode", required=True,
                   help="Project mode (full / analyze / plan / plan_only). "
                        "Gate is a no-op for anything except plan_only.")
    p.add_argument("--verdicts", required=True,
                   help="Path to the manager's plan-verdicts JSON file.")
    p.add_argument("--project-repo", required=True,
                   help="GitHub repo (owner/name) the gate applies to.")
    p.add_argument("--run-id", required=True,
                   help="Full run id including 'run-' prefix.")
    p.add_argument("--workspace", default=None,
                   help="Workspace dir for writing revision feedback files.")
    p.add_argument("--revision-count", type=int, default=0,
                   help="Current revision count (0 for first review pass).")
    p.add_argument("--api-url", default=None,
                   help="Dashboard base URL. Defaults to STATION_DASHBOARD_URL "
                        "/ STATION_WEBHOOK_URL / http://127.0.0.1:8420.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)

    outcomes = apply_plan_review_gate(
        project_mode=args.project_mode,
        verdicts_path=args.verdicts,
        project_repo=args.project_repo,
        run_id=args.run_id,
        workspace=args.workspace,
        revision_count=args.revision_count,
        api_url=args.api_url,
    )

    # Summary JSON to stdout for the shell driver to capture.
    summary = {
        "outcomes": [
            {
                "action_kind": o.action_kind,
                "verdict_kind": o.verdict_kind,
                "issue_number": o.issue_number,
                "queue_item_id": o.queue_item_id,
                "feedback_path": o.feedback_path,
                "next_run_state": o.next_run_state,
                "posted_status_event": o.posted_status_event,
            }
            for o in outcomes
        ],
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
