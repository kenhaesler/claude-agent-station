"""Tests pinning the APPROVE / APPROVE_INTEGRATION collapse.

Before this change, APPROVE only opened a PR while APPROVE_INTEGRATION
also armed ``gh pr merge --auto --squash``. The manager's decision
tree picked APPROVE for routine completed work, so PRs piled up open
(run-20260521T210606Z produced PRs #6 and #7 in laboef1900/LCM that
sat untouched). The two verdicts have been collapsed: APPROVE now
arms auto-merge unconditionally; branch protection on the base ref
gates whether the merge happens immediately or waits on CI.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock


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


def _ok_subprocess():
    cp = MagicMock()
    cp.returncode = 0
    cp.stdout = ""
    cp.stderr = ""
    return cp


def _gh_ok(stdout: str = ""):
    from agent.gh_client import GhResult

    return GhResult(cmd=["gh"], returncode=0, stdout=stdout, stderr="")


def _gh_fail(stderr: str = "boom"):
    from agent.gh_client import GhResult

    return GhResult(cmd=["gh"], returncode=1, stdout="", stderr=stderr)


def test_approve_arms_auto_merge_squash_against_pr_url(tmp_path: Path):
    """The PR URL returned from ``gh pr create`` must be passed verbatim
    to ``gh pr merge --auto --squash``."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _gh_ok(stdout=pr_url),       # pr create
            _gh_ok("MERGEABLE"),          # pr view --json mergeable (#477)
            _gh_ok(""),                   # pr merge --auto
            _gh_ok(""),                   # issue comment
            _gh_ok(""),                   # issue close
        ]
        result = execute(_verdict(), workspace=tmp_path)

    assert result.success
    merge_args = mock_gh.call_args_list[2].args[0]
    assert merge_args[:4] == ["pr", "merge", "--auto", "--squash"]
    assert pr_url in merge_args


def test_approve_continues_when_auto_merge_arm_fails(tmp_path: Path, caplog):
    """If ``gh pr merge --auto`` fails (branch protection misconfigured,
    repo doesn't allow squash, etc.), the verdict must still report
    success — the PR exists, only the merge gate didn't get armed.
    Failure must be logged at WARNING so operators can see it."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh, \
         caplog.at_level("WARNING", logger="agent.verdict_execution"):
        mock_gh.side_effect = [
            _gh_ok(stdout=pr_url),                          # pr create
            _gh_ok("MERGEABLE"),                             # pr view (#477)
            _gh_fail("auto-merge is not allowed for this repository"),
            _gh_ok(""),                                      # issue comment
            _gh_ok(""),                                      # issue close
        ]
        result = execute(_verdict(), workspace=tmp_path)

    assert result.success is True, (
        "auto-merge arm failure must not invalidate the verdict — "
        "the PR exists and branch protection still decides the merge"
    )
    assert any(
        "auto-merge arm failed" in rec.message for rec in caplog.records
    ), "auto-merge failure must surface as WARNING"
    # Result actions record the failure so the digest shows it.
    assert any("auto failed" in a for a in result.actions)


def test_approve_integration_alias_produces_identical_call_sequence(tmp_path: Path):
    """APPROVE_INTEGRATION must hit the same git/gh calls in the same
    order as APPROVE — anything else means the alias is leaking the old
    divergent code path."""
    from agent.verdict_execution import execute
    from agent.verdict_execution import Verdict

    pr_url = "https://x/pr/1"
    seen_a, seen_b = [], []

    for kind, log in [("APPROVE", seen_a), ("APPROVE_INTEGRATION", seen_b)]:
        v = Verdict(
            project="o/r", issue_number=1, verdict=kind,
            branch="autonomous/issue-1",
            base_branch="claude-agent-station",
            reasoning="r",
        )
        with patch("agent.verdict_execution.subprocess.run",
                   return_value=_ok_subprocess()), \
             patch("agent.verdict_execution.gh_run") as mock_gh:
            mock_gh.side_effect = [
                _gh_ok(stdout=pr_url),
                _gh_ok("MERGEABLE"),  # pr view (#477)
                _gh_ok(""), _gh_ok(""), _gh_ok(""),
            ]
            execute(v, workspace=tmp_path)
        log.extend([tuple(c.args[0][:2]) for c in mock_gh.call_args_list])

    assert seen_a == seen_b, (
        f"APPROVE and APPROVE_INTEGRATION must produce the same gh call "
        f"sequence. APPROVE={seen_a}, APPROVE_INTEGRATION={seen_b}"
    )


def test_approve_integration_alias_patches_result_verdict_label(tmp_path: Path):
    """For telemetry continuity, the alias keeps ``result.verdict =
    "APPROVE_INTEGRATION"`` even though execution is identical to APPROVE."""
    from agent.verdict_execution import execute, Verdict

    pr_url = "https://x/pr/1"
    v = Verdict(
        project="o/r", issue_number=1, verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-1",
        base_branch="claude-agent-station",
        reasoning="r",
    )
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [
            _gh_ok(stdout=pr_url),
            _gh_ok("MERGEABLE"),  # pr view (#477)
            _gh_ok(""), _gh_ok(""), _gh_ok(""),
        ]
        result = execute(v, workspace=tmp_path)

    assert result.verdict == "APPROVE_INTEGRATION"
    assert result.success is True
