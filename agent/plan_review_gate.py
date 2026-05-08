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

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
