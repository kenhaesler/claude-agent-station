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

from agent.issue_splitter.heuristics import maybe_split
from agent.issue_splitter.runner import run_splitter
from agent.issue_splitter.schema import SplitDecision, SplitterError

logger = logging.getLogger(__name__)


async def maybe_run_splitter(
    issue: dict,
    *,
    run_id: str,
    repo_summary: str,
    vision: str,
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
    """
    if os.environ.get("STATION_SPLIT_ENABLED") != "1":
        return None
    heuristic = maybe_split(issue)
    if not heuristic.should_split:
        return None
    try:
        return await run_splitter(
            issue=issue,
            run_id=run_id,
            repo_summary=repo_summary,
            vision=vision,
        )
    except SplitterError as exc:
        logger.warning(
            "splitter failed for issue #%s: %s — falling back to single-issue",
            issue.get("number"),
            exc,
        )
        return None


async def execute_split_decision(
    parent: dict,
    decision: SplitDecision,
    *,
    run_id: str,
) -> None:
    """Apply a :class:`SplitDecision`: create sub-issues, comment on the
    parent, ensure the integration branch exists, and archive the
    decision on the run row.

    Wired in Task 11; the stub raises so the test for Task 10 still has
    a callable symbol to patch out.
    """
    raise NotImplementedError("Task 11 wires this")
