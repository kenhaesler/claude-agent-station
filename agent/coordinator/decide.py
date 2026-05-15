"""Pre-dispatch decision hooks for the issue splitter (#391).

The orchestrator (PR-4 will wire the call site) invokes
:func:`maybe_run_splitter` for each candidate issue before launching
the normal Agent Teams run. When the function returns a
:class:`SplitDecision`, the orchestrator hands off to
:func:`execute_split_decision` instead of running the single-issue
flow; on ``None`` it falls through to the existing single-issue path.

Why this module is the seam rather than ``station_orchestrator.py``:

- Heuristic + LLM dispatch + DB write are pure I/O that the
  orchestrator shouldn't know about. Keeping them here means the
  orchestrator's call site is a one-liner (``decision = await
  maybe_run_splitter(...)``) and the splitter can be unit-tested in
  isolation without spinning up the orchestrator.
- The hook is feature-gated by ``STATION_SPLIT_ENABLED=1``; with the
  flag off the module is a no-op so PR-3 ships safely with the call
  site stubbed in PR-4.
"""
from __future__ import annotations

import logging
import os

from agent.issue_splitter.gh_adapter import GhAdapter
from agent.issue_splitter.github_ops import (
    add_backlink_comment,
    create_sub_issues,
)
from agent.issue_splitter.heuristics import maybe_split
from agent.issue_splitter.runner import run_splitter
from agent.issue_splitter.schema import SplitDecision, SplitterError

logger = logging.getLogger(__name__)


def _gh_client() -> GhAdapter:
    """Factory seam for tests.

    Tests patch ``agent.coordinator.decide._gh_client`` to inject a
    ``MagicMock`` adapter without going through GitHub.
    """
    return GhAdapter()


def _ensure_integration_branch(repo: str, parent_number: int) -> str:
    """Create ``integration/issue-<N>`` off ``dev`` if it doesn't exist.

    Kept as a top-level function (rather than inlined) so the test for
    :func:`execute_split_decision` can patch it independently — branch
    creation hits two ``gh api`` calls in the happy path and isn't worth
    re-asserting in every execution test.
    """
    branch = f"integration/issue-{parent_number}"
    _gh_client().ensure_branch(repo, branch, from_branch="dev")
    return branch


async def maybe_run_splitter(
    issue: dict,
    *,
    run_id: str,
    repo_summary: str,
    vision: str,
    cwd: str | None = None,
) -> SplitDecision | None:
    """Pre-dispatch hook. Returns a :class:`SplitDecision` when the issue
    should be split, ``None`` otherwise (caller falls through to the
    normal single-issue dispatch path).

    Feature-gated by ``STATION_SPLIT_ENABLED=1``. When the flag is unset
    or any other value, the function returns ``None`` without consulting
    the heuristic — no LLM call, no GitHub access.

    ``SplitterError`` from the SDK runner is caught and logged at WARNING:
    a failed split should not block the normal pickup path. The
    single-issue flow remains a safe fallback.

    A successful splitter call that returns an empty-proposals decision
    ("don't split; run as-is") is collapsed to ``None`` here, so callers
    have a single signal to branch on: ``None`` → run the parent
    single-issue, non-``None`` → hand off to :func:`execute_split_decision`.
    Without this collapse, callers would have to also check
    ``decision.proposals`` and ``execute_split_decision`` would happily
    label the parent ``split`` despite no actual decomposition.

    ``cwd`` is forwarded to :func:`run_splitter`; pass the project's
    checkout root so the splitter's read-only tool set inspects the
    right tree (not the launcher's ``/app`` in container mode).
    """
    if os.environ.get("STATION_SPLIT_ENABLED") != "1":
        return None
    heuristic = maybe_split(issue)
    if not heuristic.should_split:
        return None
    try:
        decision = await run_splitter(
            issue=issue,
            run_id=run_id,
            repo_summary=repo_summary,
            vision=vision,
            cwd=cwd,
        )
    except SplitterError as exc:
        logger.warning(
            "splitter failed for issue #%s: %s — falling back to single-issue",
            issue.get("number"),
            exc,
        )
        return None
    if not decision.proposals:
        # Splitter looked at the issue and said "no, run as-is" (empty
        # array). Surface that as a fall-through rather than a decision
        # the caller has to special-case.
        return None
    return decision


async def execute_split_decision(
    parent: dict,
    decision: SplitDecision,
    *,
    run_id: str,
) -> None:
    """Apply a :class:`SplitDecision`: create sub-issues, comment on the
    parent, label the parent ``split``, ensure the integration branch
    exists, and archive the decision on the run row.

    Ordering rationale:

    1. Create sub-issues first — they're the irreversible side effect
       the operator cares about most. If a later step fails, the
       sub-issues still exist (with the ``splitter-proposed`` label, so
       they sit pending review rather than dispatch automatically).
    2. Post the backlink comment so the parent has a discoverable audit
       trail even if the next steps fail.
    3. Label the parent ``split`` so the dispatcher's next tick skips
       it. Doing this before the DB write is fine — the worst case is
       the DB row lacks ``run_kind`` but GitHub state is consistent.
    4. Ensure the integration branch exists last. The branch is only
       needed once the first sub-run wants to merge; deferring it gives
       earlier failures a chance to surface without leaving dangling
       branches.
    5. Persist the split decision on the run row for observability.

    Refuses to execute on an empty-proposals decision: ``maybe_run_splitter``
    is the load-bearing gate (it collapses "splitter said no" to ``None``),
    but a future caller bypassing that path could pass an empty decision
    directly. Without this guard the function would label the parent
    ``split`` and create an empty integration branch — visible damage on
    a parent issue that the splitter explicitly declined to decompose.
    """
    if not decision.proposals:
        raise ValueError(
            "execute_split_decision called with empty proposals — use "
            "maybe_run_splitter's None return as the 'do not split' signal"
        )
    gh = _gh_client()
    created = create_sub_issues(parent, decision.proposals, gh)
    sub_numbers = [c["number"] for c in created]
    add_backlink_comment(
        parent_repo=parent["repo"],
        parent_number=parent["number"],
        sub_numbers=sub_numbers,
        gh=gh,
    )
    owner, repo = parent["repo"].split("/", 1)
    gh.add_labels(owner, repo, parent["number"], ["split"])
    _ensure_integration_branch(parent["repo"], parent["number"])

    # Persist the split decision on the run row so the dashboard can
    # render the decomposition without re-fetching GitHub. The DB write
    # is best-effort: the GitHub side effects above are the
    # source-of-truth, and a DB failure here would re-trigger a
    # GitHub-side duplicate on retry. Log and continue.
    await _persist_split_decision(run_id, parent["number"], sub_numbers,
                                  list(decision.warnings))


async def _persist_split_decision(
    run_id: str,
    parent_number: int,
    sub_numbers: list[int],
    warnings: list[str],
) -> None:
    """Best-effort archival write of the splitter decision payload.

    Top-level helper (rather than inline) so callers can patch it in
    tests that aren't running against a live DB. Failures are logged at
    WARNING and swallowed: this is observability, not control flow.

    Catches only the failure shapes a DB outage actually produces
    (``SQLAlchemyError`` and ``OSError``) rather than a blanket
    ``Exception`` — programming errors (``NameError``/``AttributeError``)
    should crash loudly so they get fixed, not get silently logged.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        from app.database import async_session
        from app.models import Run
        from sqlalchemy import update
        async with async_session() as db:
            await db.execute(
                update(Run).where(Run.run_id == run_id).values(
                    run_kind="split-decision",
                    split_decision_json={
                        "parent_number": parent_number,
                        "sub_numbers": sub_numbers,
                        "warnings": warnings,
                    },
                )
            )
            await db.commit()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning(
            "failed to persist split decision for run=%s parent=#%d: %s",
            run_id, parent_number, exc,
        )
