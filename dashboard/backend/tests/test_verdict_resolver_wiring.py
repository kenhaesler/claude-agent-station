"""Tests for the conflict-resolver wiring in :mod:`agent.verdict_execution`.

Background: PR #6 + #7 in laboef1900/LCM on 2026-05-21 surfaced the
real failure mode — once one auto-merge-armed APPROVE lands, any
sibling PR targeting the same base goes CONFLICTING and the
auto-merge stalls. The conflict resolver exists (#476 fixed Phase 3)
but wasn't called by the autonomous flow.

These tests pin the new wiring: after ``gh pr create`` and before
arming auto-merge, the executor checks the PR's mergeability state
and invokes ``agent/scripts/resolve-conflicts.sh`` when CONFLICTING.
Best-effort: resolver failures are logged + recorded but do not
invalidate the verdict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _verdict(branch: str = "autonomous/issue-42"):
    from agent.verdict_execution import Verdict

    return Verdict(
        project="owner/repo",
        issue_number=42,
        verdict="APPROVE",
        branch=branch,
        base_branch="claude-agent-station",
        reasoning="ok",
        mode="full",
    )


def _ok_subprocess(stdout: str = "", stderr: str = ""):
    cp = MagicMock()
    cp.returncode = 0
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _fail_subprocess(stderr: str, returncode: int = 1):
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = ""
    cp.stderr = stderr
    return cp


def _gh_ok(stdout: str = ""):
    from agent.gh_client import GhResult

    return GhResult(cmd=["gh"], returncode=0, stdout=stdout, stderr="")


# ── _pr_number_from_url ────────────────────────────────────────────────


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/owner/repo/pull/100", 100),
    ("https://github.com/owner/repo/pull/7", 7),
    ("https://github.com/owner/repo/pull/9999999", 9999999),
])
def test_pr_number_extracted_from_canonical_urls(url, expected):
    from agent.verdict_execution import _pr_number_from_url
    assert _pr_number_from_url(url) == expected


@pytest.mark.parametrize("url", [
    "",
    None,
    "https://github.com/owner/repo",            # no /pull/ segment
    "https://github.com/owner/repo/issues/42",  # wrong segment
    "garbage",
])
def test_pr_number_returns_none_on_malformed_url(url):
    from agent.verdict_execution import _pr_number_from_url
    assert _pr_number_from_url(url) is None


# ── _poll_pr_mergeable ─────────────────────────────────────────────────


def test_mergeable_returns_state_on_first_definite_response():
    """Settled state comes back immediately without any sleeping."""
    from agent.verdict_execution import _poll_pr_mergeable

    with patch("agent.verdict_execution.gh_run",
               return_value=_gh_ok("MERGEABLE")) as mock_gh, \
         patch("agent.verdict_execution.time.sleep") as mock_sleep:
        result = _poll_pr_mergeable("https://x/pull/1", env=None)

    assert result == "MERGEABLE"
    mock_gh.assert_called_once()
    mock_sleep.assert_not_called()


def test_mergeable_polls_until_state_settles():
    """UNKNOWN → CONFLICTING across two attempts. Second call must
    follow a ``time.sleep`` so we're not spinning the gh CLI."""
    from agent.verdict_execution import _poll_pr_mergeable
    from agent.gh_client import GhResult

    responses = [
        GhResult(cmd=["gh"], returncode=0, stdout="UNKNOWN", stderr=""),
        GhResult(cmd=["gh"], returncode=0, stdout="CONFLICTING", stderr=""),
    ]
    with patch("agent.verdict_execution.gh_run", side_effect=responses), \
         patch("agent.verdict_execution.time.sleep") as mock_sleep:
        result = _poll_pr_mergeable(
            "https://x/pull/1", env=None,
            max_attempts=3, delay_s=0.01,
        )

    assert result == "CONFLICTING"
    assert mock_sleep.call_count == 1


def test_mergeable_returns_none_on_gh_error():
    """A non-zero ``gh pr view`` exits the poll immediately with None
    so the caller can degrade gracefully."""
    from agent.verdict_execution import _poll_pr_mergeable
    from agent.gh_client import GhResult

    with patch("agent.verdict_execution.gh_run",
               return_value=GhResult(cmd=["gh"], returncode=1,
                                     stdout="", stderr="API down")):
        result = _poll_pr_mergeable("https://x/pull/1", env=None)

    assert result is None


# ── _resolve_pr_conflict_if_needed (end-to-end through execute) ────────


def test_approve_skips_resolver_when_pr_is_mergeable(tmp_path: Path):
    """``mergeable=MERGEABLE`` → resolver script is NEVER invoked."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"
    # Capture subprocess.run to confirm the resolver script never runs.
    sp_calls: list[list[str]] = []

    def _sp_spy(cmd, *args, **kwargs):  # noqa: ANN001
        sp_calls.append(list(cmd))
        return _ok_subprocess()

    with patch("agent.verdict_execution.subprocess.run", side_effect=_sp_spy), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _gh_ok(pr_url),       # pr create
            _gh_ok("MERGEABLE"),   # pr view --json mergeable
            _gh_ok(""),            # pr merge --auto
            _gh_ok(""),            # issue comment
            _gh_ok(""),            # issue close
        ]
        result = execute(_verdict(), workspace=tmp_path)

    assert result.success
    # Subprocess calls: git push only — no resolve-conflicts.sh invocation.
    sp_kinds = [c[0] for c in sp_calls]
    assert "bash" not in sp_kinds, (
        f"resolver script must NOT be invoked on MERGEABLE PRs; "
        f"subprocess calls were: {sp_calls}"
    )


def test_approve_invokes_resolver_when_pr_is_conflicting(tmp_path: Path):
    """``mergeable=CONFLICTING`` → executor checks out the feature
    branch then shells out to ``resolve-conflicts.sh`` with the right
    argv. Resolver-success records a positive action."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"
    sp_calls: list[list[str]] = []

    def _sp_spy(cmd, *args, **kwargs):  # noqa: ANN001
        sp_calls.append(list(cmd))
        return _ok_subprocess()

    with patch("agent.verdict_execution.subprocess.run", side_effect=_sp_spy), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _gh_ok(pr_url),        # pr create
            _gh_ok("CONFLICTING"),  # pr view --json mergeable
            _gh_ok(""),             # pr merge --auto
            _gh_ok(""),             # issue comment
            _gh_ok(""),             # issue close
        ]
        result = execute(
            _verdict(), workspace=tmp_path, run_id="run-test",
        )

    # Subprocess sequence: git push, git checkout, bash resolve-conflicts.sh
    sp_first_args = [c[:3] for c in sp_calls]
    assert ["git", "push", "-u"] in sp_first_args, "git push must run first"
    assert any(c[:2] == ["git", "checkout"]
               and "autonomous/issue-42" in c
               for c in sp_calls), (
        f"resolver path must checkout the feature branch first; "
        f"subprocess calls were: {sp_calls}"
    )

    resolver_calls = [c for c in sp_calls if c and c[0] == "bash"]
    assert resolver_calls, (
        f"resolver script must be invoked on CONFLICTING PRs; "
        f"subprocess calls were: {sp_calls}"
    )
    resolver_argv = resolver_calls[0]
    # Script path
    assert resolver_argv[1].endswith("resolve-conflicts.sh")
    # Flags
    assert "--branch" in resolver_argv and "autonomous/issue-42" in resolver_argv
    assert "--base" in resolver_argv and "claude-agent-station" in resolver_argv
    assert "--repo" in resolver_argv and "owner/repo" in resolver_argv
    assert "--triggered-by" in resolver_argv and "at_merge" in resolver_argv
    assert "--pr" in resolver_argv and "100" in resolver_argv
    assert "--run-id" in resolver_argv and "run-test" in resolver_argv

    assert any("conflict resolver: resolved" in a for a in result.actions)
    assert result.success


def test_approve_records_resolver_failure_but_keeps_verdict_success(
    tmp_path: Path, caplog,
):
    """A non-zero resolver exit code must be logged + recorded in
    ``result.actions`` but the verdict stays successful — the PR
    exists, a human can take over."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"

    def _sp_spy(cmd, *args, **kwargs):  # noqa: ANN001
        if cmd and cmd[0] == "bash":
            # Simulate resolver exit code 10 (tests-failed-after-rounds)
            return _fail_subprocess("manager rejected", returncode=10)
        return _ok_subprocess()

    with patch("agent.verdict_execution.subprocess.run", side_effect=_sp_spy), \
         patch("agent.verdict_execution.gh_run") as mock_gh, \
         caplog.at_level("WARNING", logger="agent.verdict_execution"):
        mock_gh.side_effect = [
            _gh_ok(pr_url),
            _gh_ok("CONFLICTING"),
            _gh_ok(""), _gh_ok(""), _gh_ok(""),
        ]
        result = execute(_verdict(), workspace=tmp_path)

    assert result.success is True, (
        "resolver failure must not invalidate the verdict — "
        "the PR exists for human takeover"
    )
    assert any("exit=10" in a for a in result.actions)
    assert any(
        "conflict resolver exited 10" in rec.message
        for rec in caplog.records
    )


def test_approve_skips_resolver_when_checkout_fails(
    tmp_path: Path, caplog,
):
    """If we can't ``git checkout`` the feature branch in the workspace,
    skip the resolver rather than invoking it on the wrong HEAD
    (which is how the manual 2026-05-21 attempt rebased main by accident)."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"
    sp_calls: list[list[str]] = []

    def _sp_spy(cmd, *args, **kwargs):  # noqa: ANN001
        sp_calls.append(list(cmd))
        if cmd[:2] == ["git", "checkout"]:
            return _fail_subprocess("conflicting working-tree files")
        return _ok_subprocess()

    with patch("agent.verdict_execution.subprocess.run", side_effect=_sp_spy), \
         patch("agent.verdict_execution.gh_run") as mock_gh, \
         caplog.at_level("WARNING", logger="agent.verdict_execution"):
        mock_gh.side_effect = [
            _gh_ok(pr_url),
            _gh_ok("CONFLICTING"),
            _gh_ok(""), _gh_ok(""), _gh_ok(""),
        ]
        result = execute(_verdict(), workspace=tmp_path)

    assert result.success
    # Resolver script never invoked
    assert not any(c and c[0] == "bash" for c in sp_calls)
    # Action recorded
    assert any("checkout autonomous/issue-42 failed" in a
               for a in result.actions)
