"""Regression tests for integration-branch routing in APPROVE/PR verdicts.

The bug: PR #475 (commit 68dca3c) collapsed the separate
``APPROVE_INTEGRATION`` verdict into ``execute_approve``. Pre-collapse,
``APPROVE_INTEGRATION`` opened PRs against the configured integration
branch; post-collapse, ``execute_approve`` opens PRs against
``verdict.base_branch`` (the employee-reported value, which defaults to
``main`` for repos with no ``CLAUDE.md``). On a live run against
``laboef1900/LCM`` the executor squash-merged PR #9 into ``main``
despite ``config.integration.enabled = true`` and
``config.integration.dev_branch = "claude-agent-station"``.

The fix: ``execute_approve`` and ``execute_pr`` now honor a non-None
``dev_branch`` kwarg by routing the PR base to it, bootstrapping the
integration branch on origin (from the verdict's reported base) when
it doesn't exist yet. ``project_loop`` only passes ``dev_branch`` when
``integration.enabled`` is true, so opt-out repos keep their legacy
behaviour.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _verdict(verdict_kind: str = "APPROVE", **overrides):
    from agent.verdict_execution import Verdict

    fields = dict(
        project="owner/repo",
        issue_number=42,
        verdict=verdict_kind,
        branch="autonomous/issue-42",
        base_branch="main",
        reasoning="Looks good",
        mode="full",
    )
    fields.update(overrides)
    return Verdict(**fields)


def _ok_gh_result(stdout: str = ""):
    from agent.gh_client import GhResult

    return GhResult(cmd=["gh"], returncode=0, stdout=stdout, stderr="")


def _subprocess_factory(*, ls_remote_stdout: str = "", bootstrap_ok: bool = True):
    """Build a side_effect for ``subprocess.run`` that returns sensible
    results for each git call the executor makes in integration mode.

    Call order (when integration is enabled and branch is missing):
      1. git push origin <feature_branch>     → ok
      2. git ls-remote --heads origin <dev>   → stdout configurable
      3. git fetch origin <fallback_base>     → ok (only if missing)
      4. git push origin refs/remotes/...     → bootstrap_ok configurable
    """
    calls: list[list[str]] = []

    def side_effect(args, **kwargs):
        calls.append(args)
        cp = MagicMock()
        cp.stdout = ""
        cp.stderr = ""
        cp.returncode = 0
        if args[:3] == ["git", "ls-remote", "--heads"]:
            cp.stdout = ls_remote_stdout
            return cp
        if args[:2] == ["git", "push"] and "refs/remotes/origin/" in (
            args[3] if len(args) > 3 else ""
        ):
            if not bootstrap_ok:
                cp.returncode = 1
                cp.stderr = "remote rejected"
            return cp
        return cp

    return side_effect, calls


# ── APPROVE: integration branch is honoured when dev_branch is set ────


def test_approve_targets_integration_branch_when_dev_branch_passed(tmp_path):
    """The whole point of the fix — dev_branch overrides verdict.base_branch."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/200"
    sp_side, sp_calls = _subprocess_factory()

    with patch("agent.verdict_execution.subprocess.run", side_effect=sp_side), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _ok_gh_result(stdout=pr_url),       # gh pr create
            _ok_gh_result(stdout="MERGEABLE"),  # gh pr view --json mergeable
            _ok_gh_result(),                    # gh pr merge --auto
            _ok_gh_result(),                    # gh issue comment
            _ok_gh_result(),                    # gh issue close
        ]
        result = execute(
            _verdict("APPROVE", base_branch="main"),
            workspace=tmp_path,
            run_id="run-1",
            dev_branch="claude-agent-station",
        )

    assert result.success
    pr_args = mock_gh.call_args_list[0].args[0]
    assert pr_args[:2] == ["pr", "create"]
    # The PR must target the integration branch, NOT main.
    assert pr_args[pr_args.index("--base") + 1] == "claude-agent-station"


def test_approve_falls_back_to_base_branch_when_dev_branch_is_none(tmp_path):
    """Legacy behaviour: integration disabled → trust verdict.base_branch."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/201"

    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh.side_effect = [
            _ok_gh_result(stdout=pr_url),
            _ok_gh_result(stdout="MERGEABLE"),
            _ok_gh_result(),
            _ok_gh_result(),
            _ok_gh_result(),
        ]
        result = execute(
            _verdict("APPROVE", base_branch="main"),
            workspace=tmp_path,
            run_id="run-1",
            dev_branch=None,
        )

    assert result.success
    pr_args = mock_gh.call_args_list[0].args[0]
    assert pr_args[pr_args.index("--base") + 1] == "main"


def test_approve_bootstraps_missing_integration_branch(tmp_path):
    """When the integration branch doesn't exist on origin, the executor
    must create it from the verdict's reported base before opening the PR."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/202"
    sp_side, sp_calls = _subprocess_factory(ls_remote_stdout="")  # missing

    with patch("agent.verdict_execution.subprocess.run", side_effect=sp_side), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _ok_gh_result(stdout=pr_url),
            _ok_gh_result(stdout="MERGEABLE"),
            _ok_gh_result(),
            _ok_gh_result(),
            _ok_gh_result(),
        ]
        result = execute(
            _verdict("APPROVE", base_branch="main"),
            workspace=tmp_path,
            dev_branch="claude-agent-station",
        )

    assert result.success
    # The bootstrap push must have happened with the right refspec.
    bootstrap_calls = [
        c for c in sp_calls
        if len(c) >= 4
        and c[:2] == ["git", "push"]
        and "refs/remotes/origin/main:refs/heads/claude-agent-station" in c
    ]
    assert bootstrap_calls, (
        f"expected bootstrap push, got subprocess calls: {sp_calls}"
    )


def test_approve_skips_bootstrap_when_integration_branch_exists(tmp_path):
    """Idempotency: if the integration branch already exists upstream we
    must not push a fresh ref over it."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/203"
    # ls-remote returns content → branch exists.
    sp_side, sp_calls = _subprocess_factory(
        ls_remote_stdout="abc123\trefs/heads/claude-agent-station\n"
    )

    with patch("agent.verdict_execution.subprocess.run", side_effect=sp_side), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _ok_gh_result(stdout=pr_url),
            _ok_gh_result(stdout="MERGEABLE"),
            _ok_gh_result(),
            _ok_gh_result(),
            _ok_gh_result(),
        ]
        execute(
            _verdict("APPROVE", base_branch="main"),
            workspace=tmp_path,
            dev_branch="claude-agent-station",
        )

    # No call should be `git push origin refs/remotes/origin/main:...`.
    bootstrap_pushes = [
        c for c in sp_calls
        if len(c) >= 4
        and c[:2] == ["git", "push"]
        and any("refs/remotes/origin/main:refs/heads/" in arg for arg in c)
    ]
    assert not bootstrap_pushes, (
        f"unexpected bootstrap push: {bootstrap_pushes}"
    )


def test_approve_aborts_when_bootstrap_fails(tmp_path):
    """If the integration-branch bootstrap fails, the verdict fails too —
    don't fall through to opening a PR against main."""
    from agent.verdict_execution import execute

    sp_side, sp_calls = _subprocess_factory(
        ls_remote_stdout="", bootstrap_ok=False,
    )

    with patch("agent.verdict_execution.subprocess.run", side_effect=sp_side), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(
            _verdict("APPROVE", base_branch="main"),
            workspace=tmp_path,
            dev_branch="claude-agent-station",
        )

    assert not result.success
    assert "integration branch" in (result.error or "")
    # Critically: gh pr create must NOT have been called — silent fallback
    # to opening a PR against main is exactly the bug we're guarding against.
    create_calls = [
        c for c in mock_gh.call_args_list
        if c.args and c.args[0][:2] == ["pr", "create"]
    ]
    assert not create_calls, (
        "executor must not open a PR when integration-branch bootstrap fails"
    )


# ── execute_pr (draft PR) honours dev_branch identically ──────────────


def test_pr_verdict_targets_integration_branch_when_dev_branch_passed(tmp_path):
    """The draft-PR path takes the same routing as APPROVE so reviewers
    aren't surprised by the base depending on the verdict kind."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/300"
    sp_side, _ = _subprocess_factory()

    with patch("agent.verdict_execution.subprocess.run", side_effect=sp_side), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _ok_gh_result(stdout=pr_url),  # gh pr create --draft
            _ok_gh_result(),               # issue comment
        ]
        execute(
            _verdict("PR", base_branch="main"),
            workspace=tmp_path,
            dev_branch="claude-agent-station",
        )

    pr_args = mock_gh.call_args_list[0].args[0]
    assert pr_args[:2] == ["pr", "create"]
    assert pr_args[pr_args.index("--base") + 1] == "claude-agent-station"
    assert "--draft" in pr_args
