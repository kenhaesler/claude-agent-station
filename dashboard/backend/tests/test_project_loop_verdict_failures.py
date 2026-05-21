"""Tests for :func:`agent.project_loop._execute_one_verdict`.

Background: two live runs against ``laboef1900/LCM`` on 2026-05-21
(run-20260521T142800Z, run-20260521T151955Z) recorded verdict APPROVE for
every issue while opening zero PRs. Root cause was two-fold:

- the manager hallucinated ``project="claude-agent-station/LCM"`` (or
  just ``"LCM"``), so ``gh pr create --repo <that>`` 404'd; and
- the executor returned ``ExecutionResult(success=False, error=…)`` for
  that 404 but the loop discarded the result, recording APPROVE anyway.

The helper extracted in :func:`_execute_one_verdict` exists to fix both.
These tests pin that behavior so a future refactor cannot quietly
regress to "all green, no PRs".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def _verdict(verdict_kind: str = "APPROVE", project: str = "wrong-owner/wrong"):
    from agent.verdict_execution import Verdict

    return Verdict(
        project=project,
        issue_number=42,
        verdict=verdict_kind,
        branch="autonomous/issue-42",
        base_branch="main",
        reasoning="ok",
        mode="full",
    )


def _result(success: bool, **overrides):
    from agent.verdict_execution import ExecutionResult

    fields = dict(
        verdict="APPROVE",
        project="ignored-here",
        issue_number=42,
        success=success,
    )
    fields.update(overrides)
    return ExecutionResult(**fields)


def test_returned_failure_is_surfaced_as_error_decision(tmp_path: Path):
    """``ExecutionResult.success=False`` must produce an ERROR dict, not
    a quiet APPROVE. Reproduces the 2026-05-21 silent failure."""
    from agent.project_loop import _execute_one_verdict

    captured = _result(False, error="gh pr create failed: 404 repo")

    with patch("agent.verdict_execution.execute", return_value=captured):
        out = _execute_one_verdict(
            _verdict("APPROVE"),
            project_repo="laboef1900/LCM",
            workspace_path=str(tmp_path),
            run_id="run-test",
            dev_branch="autonomous/dev",
            env={},
        )

    assert out["decision"] == "ERROR"
    assert "404" in out["error"]
    # Preserve the intended verdict so the digest can show what was attempted.
    assert out["intended_decision"] == "APPROVE"


def test_successful_execution_records_verdict_with_pr_url(tmp_path: Path):
    """``success=True`` keeps the manager's verdict (APPROVE/PR/etc.) and
    carries the ``pr_url`` for downstream reporting."""
    from agent.project_loop import _execute_one_verdict

    captured = _result(True, pr_url="https://github.com/laboef1900/LCM/pull/7")

    with patch("agent.verdict_execution.execute", return_value=captured):
        out = _execute_one_verdict(
            _verdict("APPROVE"),
            project_repo="laboef1900/LCM",
            workspace_path=str(tmp_path),
            run_id="run-test",
            dev_branch="autonomous/dev",
            env={},
        )

    assert out["decision"] == "APPROVE"
    assert out["pr_url"] == "https://github.com/laboef1900/LCM/pull/7"
    assert "error" not in out


def test_project_slug_is_overridden_before_executor_dispatch(tmp_path: Path):
    """The manager's ``verdict.project`` is replaced by the canonical
    ``project_repo`` before ``execute_verdict`` sees it. Defeats the
    "claude-agent-station/LCM" hallucination from 2026-05-21."""
    from agent.project_loop import _execute_one_verdict

    seen: dict = {}

    def _spy(verdict, **kwargs):  # noqa: ANN001
        seen["project"] = verdict.project
        return _result(True, pr_url="https://x/pr/1", project=verdict.project)

    bad = _verdict("APPROVE", project="claude-agent-station/LCM")

    with patch("agent.verdict_execution.execute", side_effect=_spy):
        _execute_one_verdict(
            bad,
            project_repo="laboef1900/LCM",
            workspace_path=str(tmp_path),
            run_id="run-test",
            dev_branch="autonomous/dev",
            env={},
        )

    assert seen["project"] == "laboef1900/LCM"
    # And the mutation persists on the verdict object so later callers
    # (digest, webhook) see the canonical slug too.
    assert bad.project == "laboef1900/LCM"


def test_raised_exception_is_caught_and_recorded_as_error(tmp_path: Path):
    """Existing behavior preserved: a raise from the executor (e.g.
    TypeError from a kwarg mismatch — the 2026-05-15 incident) is caught
    and recorded as ERROR rather than crashing the loop."""
    from agent.project_loop import _execute_one_verdict

    with patch("agent.verdict_execution.execute",
               side_effect=TypeError("missing kwarg")):
        out = _execute_one_verdict(
            _verdict("APPROVE"),
            project_repo="laboef1900/LCM",
            workspace_path=str(tmp_path),
            run_id="run-test",
            dev_branch="autonomous/dev",
            env={},
        )

    assert out["decision"] == "ERROR"
    assert "TypeError" in out["error"]
    assert "missing kwarg" in out["error"]
