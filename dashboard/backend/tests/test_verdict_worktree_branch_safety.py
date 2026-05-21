"""Tests for the worktree-isolation-branch safety net in
:mod:`agent.verdict_execution`.

Background: the orchestrator creates one worktree per teammate role on a
private branch named ``worktree/<role>-<run_id_prefix>``. Those branches
exist only inside the per-role worktree checkout — pushing them from the
base workspace fails with a confusing ``src refspec does not match any``.
The 2026-05-21 run-20260521T175359Z hit this when teammates skipped
``git checkout -b <feature-branch>`` and committed straight on the
worktree branch.

These tests pin the helper that refuses such verdicts up-front with a
clear error string instead of letting them flow through to the
opaque-git-error path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _verdict(branch: str, kind: str = "APPROVE"):
    from agent.verdict_execution import Verdict

    return Verdict(
        project="owner/repo",
        issue_number=42,
        verdict=kind,
        branch=branch,
        base_branch="claude-agent-station",
        reasoning="ok",
        mode="full",
    )


# ── _is_worktree_isolation_branch ──────────────────────────────────────


@pytest.mark.parametrize("branch", [
    "worktree/backend-20260521",
    "worktree/frontend-20260521",
    "worktree/qa-20260521",
    "worktree/backend-abcdef12",
    "worktree/qa-12345678",
])
def test_detects_orchestrator_worktree_branches(branch):
    from agent.verdict_execution import _is_worktree_isolation_branch

    assert _is_worktree_isolation_branch(branch) is True


@pytest.mark.parametrize("branch", [
    "autonomous/issue-42",
    "feature/issue-3-rest-api",
    "fix/login-bug",
    "main",
    "claude-agent-station",
    "worktree/notaroleshape",          # missing dash + id segment
    "feature/worktree-isolation",      # not in worktree/ namespace
    "worktree/backend-",               # trailing-empty id
    "",
])
def test_does_not_flag_normal_branches(branch):
    from agent.verdict_execution import _is_worktree_isolation_branch

    assert _is_worktree_isolation_branch(branch) is False


# ── execute_approve / execute_pr / execute_approve_integration ─────────


def test_execute_approve_refuses_worktree_branch_without_calling_git(tmp_path):
    """APPROVE on a worktree-isolation branch must fail fast with the
    canonical error message and never invoke git or gh."""
    from agent.verdict_execution import execute

    v = _verdict("worktree/backend-20260521", "APPROVE")
    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(v, workspace=tmp_path)

    assert result.success is False
    assert "worktree" in (result.error or "").lower()
    assert "isolation" in (result.error or "").lower()
    assert "worktree/backend-20260521" in (result.error or "")
    mock_sp.assert_not_called()
    mock_gh.assert_not_called()


def test_execute_pr_refuses_worktree_branch_without_calling_git(tmp_path):
    """Same guarantee for PR (draft) verdicts."""
    from agent.verdict_execution import execute

    v = _verdict("worktree/frontend-20260521", "PR")
    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(v, workspace=tmp_path)

    assert result.success is False
    assert "worktree/frontend-20260521" in (result.error or "")
    mock_sp.assert_not_called()
    mock_gh.assert_not_called()


def test_execute_approve_integration_refuses_worktree_branch(tmp_path):
    """APPROVE_INTEGRATION pushes against the dev branch with auto-merge
    armed — same isolation check applies before the push."""
    from agent.verdict_execution import execute

    v = _verdict("worktree/qa-20260521", "APPROVE_INTEGRATION")
    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(v, workspace=tmp_path, dev_branch="claude-agent-station")

    assert result.success is False
    assert "worktree/qa-20260521" in (result.error or "")
    mock_sp.assert_not_called()
    mock_gh.assert_not_called()


def test_execute_approve_still_pushes_normal_branches(tmp_path):
    """Sanity: the safety net must not block legitimate feature branches.
    Mirrors the existing happy-path test in test_verdict_execution.py."""
    from agent.verdict_execution import execute
    from agent.gh_client import GhResult

    ok_sp = MagicMock(returncode=0, stdout="", stderr="")
    ok_gh = GhResult(cmd=["gh"], returncode=0, stdout="https://x/pr/1", stderr="")

    with patch("agent.verdict_execution.subprocess.run", return_value=ok_sp), \
         patch("agent.verdict_execution.gh_run",
               return_value=ok_gh) as mock_gh:
        result = execute(_verdict("autonomous/issue-42", "APPROVE"),
                         workspace=tmp_path)

    assert result.success is True
    # First gh call is pr create — confirms the safety net did NOT short-circuit.
    assert mock_gh.call_args_list[0].args[0][:2] == ["pr", "create"]
