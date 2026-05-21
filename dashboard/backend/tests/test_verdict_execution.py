"""Tests for agent.verdict_execution (#363, #388).

These tests pin the per-decision argv shape so reviewers can diff
against the bash invocations in agent/scripts/run-manager.sh
(~lines 2200–2500). Drift here means the Python module would push
code, label issues, or comment in subtly different ways from the
bash it replaces.

Issue #388 adds APPROVE_INTEGRATION verdict: non-draft PR against
integration branch with auto-merge armed, CI gates the merge.
"""

from __future__ import annotations

import pytest

from pathlib import Path
from unittest.mock import patch, MagicMock


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


def _fail_gh_result(stderr: str = "boom"):
    from agent.gh_client import GhResult

    return GhResult(cmd=["gh"], returncode=1, stdout="", stderr=stderr)


def _ok_subprocess():
    cp = MagicMock()
    cp.returncode = 0
    cp.stdout = ""
    cp.stderr = ""
    return cp


def _fail_subprocess(stderr: str = "permission denied"):
    cp = MagicMock()
    cp.returncode = 1
    cp.stdout = ""
    cp.stderr = stderr
    return cp


# ── APPROVE ────────────────────────────────────────────────────────────


def test_approve_pushes_branch_then_creates_pr_then_arms_auto_merge_then_comments(tmp_path):
    """APPROVE collapsed with APPROVE_INTEGRATION (2026-05-21 follow-up):
    every approve now arms ``gh pr merge --auto --squash`` so branch
    protection on the base ref decides when the PR actually lands.
    Pin the four gh calls in order."""
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()) as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.side_effect = [_ok_gh_result(stdout=pr_url),
                               _ok_gh_result(stdout=""),   # gh pr merge --auto
                               _ok_gh_result(stdout=""),   # issue comment
                               _ok_gh_result(stdout="")]   # gh issue close (#460)
        result = execute(_verdict("APPROVE"), workspace=tmp_path, run_id="run-1")

    assert result.success
    assert result.pr_url == pr_url
    # Git push as the first subprocess call
    push_args = mock_sp.call_args_list[0].args[0]
    assert push_args[:2] == ["git", "push"]
    assert "autonomous/issue-42" in push_args
    # gh pr create as the first gh call
    pr_args = mock_gh.call_args_list[0].args[0]
    assert pr_args[:2] == ["pr", "create"]
    assert pr_args[pr_args.index("--repo") + 1] == "owner/repo"
    assert pr_args[pr_args.index("--head") + 1] == "autonomous/issue-42"
    assert pr_args[pr_args.index("--base") + 1] == "main"
    # gh pr merge --auto --squash second
    merge_args = mock_gh.call_args_list[1].args[0]
    assert merge_args[:2] == ["pr", "merge"]
    assert "--auto" in merge_args and "--squash" in merge_args
    assert pr_url in merge_args
    # gh issue comment third
    comment_args = mock_gh.call_args_list[2].args[0]
    assert comment_args[:2] == ["issue", "comment"]
    assert "42" in comment_args
    # Result records the auto-merge action so the digest reflects it
    assert any("gh pr merge --auto --squash" in a for a in result.actions)


def test_approve_records_failure_when_git_push_fails(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_fail_subprocess("rejected")), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(_verdict("APPROVE"), workspace=tmp_path)

    assert not result.success
    assert "git push failed" in (result.error or "")
    # gh MUST NOT have been called — push failure aborts the verdict
    mock_gh.assert_not_called()


def test_approve_records_failure_when_pr_create_fails(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=[_fail_gh_result("a PR already exists")]):
        result = execute(_verdict("APPROVE"), workspace=tmp_path)

    assert not result.success
    assert "gh pr create failed" in (result.error or "")
    assert "PR already exists" in (result.error or "")


def test_approve_body_includes_closes_keyword_when_issue_present(tmp_path):
    from agent.verdict_execution import execute

    pr_url = "https://github.com/owner/repo/pull/100"
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=[_ok_gh_result(stdout=pr_url),
                            _ok_gh_result(),   # gh pr merge --auto (collapse #475)
                            _ok_gh_result(),   # issue comment
                            _ok_gh_result()]) as mock_gh:  # gh issue close (#460)
        execute(_verdict("APPROVE"), workspace=tmp_path, run_id="run-1")

    pr_args = mock_gh.call_args_list[0].args[0]
    body = pr_args[pr_args.index("--body") + 1]
    assert "Closes #42" in body
    assert "Run" not in body  # Run line goes in issue comment, not PR body
    assert "Autonomous run: run-1" in body


# ── PR (draft) ─────────────────────────────────────────────────────────


def test_pr_verdict_passes_draft_flag_by_default(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=[_ok_gh_result(stdout="url"),
                            _ok_gh_result()]) as mock_gh:
        execute(_verdict("PR"), workspace=tmp_path)

    pr_args = mock_gh.call_args_list[0].args[0]
    assert "--draft" in pr_args


def test_pr_verdict_caller_can_disable_draft(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=[_ok_gh_result(stdout="url"),
                            _ok_gh_result()]) as mock_gh:
        execute(_verdict("PR"), workspace=tmp_path, draft=False)

    pr_args = mock_gh.call_args_list[0].args[0]
    assert "--draft" not in pr_args


# ── REJECT ─────────────────────────────────────────────────────────────


def test_reject_comments_and_removes_labels_without_touching_git(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        result = execute(_verdict("REJECT"), workspace=tmp_path)

    assert result.success
    # git push MUST NOT happen on REJECT
    mock_sp.assert_not_called()
    # Exactly: 1 issue comment + 2 label removals
    invocations = [c.args[0][:3] for c in mock_gh.call_args_list]
    assert ["issue", "comment", "42"] in invocations
    label_edits = [c for c in mock_gh.call_args_list
                   if c.args[0][:2] == ["issue", "edit"]]
    assert len(label_edits) == 2
    removed_labels = {
        c.args[0][c.args[0].index("--remove-label") + 1]
        for c in label_edits
    }
    assert removed_labels == {
        "autonomous-agent/in-progress",
        "autonomous-agent/done",
    }


def test_reject_with_no_issue_number_is_a_clean_noop(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        result = execute(_verdict("REJECT", issue_number=None),
                         workspace=tmp_path)
    assert result.success
    mock_sp.assert_not_called()
    mock_gh.assert_not_called()
    assert "skip" in " ".join(result.actions).lower()


# ── SKIP ───────────────────────────────────────────────────────────────


def test_skip_comments_and_does_not_modify_anything_else(tmp_path):
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        result = execute(_verdict("SKIP"), workspace=tmp_path)

    assert result.success
    mock_sp.assert_not_called()
    # Exactly one gh call: the issue comment. No label edits, no push.
    assert len(mock_gh.call_args_list) == 1
    assert mock_gh.call_args_list[0].args[0][:2] == ["issue", "comment"]


# ── dispatcher safety ─────────────────────────────────────────────────


def test_unknown_verdict_kind_falls_back_to_reject(tmp_path):
    """If the manager produces a verdict string we don't recognise, the
    dispatcher MUST default to REJECT so we never push code on accident.
    """
    from agent.verdict_execution import execute

    with patch("agent.verdict_execution.subprocess.run") as mock_sp, \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        # type: ignore[arg-type] — deliberately wrong verdict kind
        result = execute(_verdict("UNKNOWN_KIND"), workspace=tmp_path)  # type: ignore[arg-type]

    # No git push under any circumstance for an unrecognised verdict.
    mock_sp.assert_not_called()
    # We executed REJECT — issue comment + 2 label removals
    invocations = {tuple(c.args[0][:2]) for c in mock_gh.call_args_list}
    assert ("issue", "comment") in invocations
    assert ("issue", "edit") in invocations


def test_verdict_from_dict_handles_string_and_null_issue_number():
    from agent.verdict_execution import Verdict

    # JSON-ish input with stringy issue_number
    v = Verdict.from_dict({
        "project": "x/y",
        "issue_number": "17",
        "verdict": "APPROVE",
        "branch": "feat/foo",
    })
    assert v.issue_number == 17

    # null / "None" / empty are all None
    for sentinel in (None, "null", "None", ""):
        v = Verdict.from_dict({
            "project": "x/y",
            "issue_number": sentinel,
            "verdict": "REJECT",
            "branch": "feat/foo",
        })
        assert v.issue_number is None, f"sentinel {sentinel!r} should map to None"


# ── APPROVE_INTEGRATION (issue #388) ───────────────────────────────────


def test_verdict_from_dict_accepts_approve_integration():
    """Manager output with verdict='APPROVE_INTEGRATION' must round-trip."""
    from agent.verdict_execution import Verdict

    payload = {
        "project": "owner/repo",
        "issue_number": 42,
        "verdict": "APPROVE_INTEGRATION",
        "branch": "autonomous/issue-42",
        "base_branch": "main",
        "reasoning": "Auth refactor with passing tests",
    }
    parsed = Verdict.from_dict(payload)
    assert parsed.verdict == "APPROVE_INTEGRATION"
    assert parsed.project == "owner/repo"
    assert parsed.issue_number == 42
    assert parsed.branch == "autonomous/issue-42"


def _stub_gh_ok(stdout: str = "https://github.com/owner/repo/pull/99") -> MagicMock:
    """Build a stand-in for ``gh_run`` returning a success result."""
    fake = MagicMock()
    fake.ok = True
    fake.stdout = stdout
    fake.stderr = ""
    return fake


def test_execute_approve_integration_happy_path(tmp_path: Path):
    """APPROVE_INTEGRATION is now an alias for APPROVE (collapse #475).
    The behavior is the four-call APPROVE sequence; only the result's
    ``verdict`` field is patched back to ``APPROVE_INTEGRATION`` for
    telemetry. The PR base comes from ``verdict.base_branch``, not the
    ``dev_branch`` kwarg, since the manager already chose integration
    via the employee's reported base."""
    from agent.verdict_execution import execute_approve_integration, Verdict

    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=42,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-42",
        base_branch="dev",  # employee reported the dev branch as base
        reasoning="Auth change; tests pass; CI gates merge.",
    )

    pr_url = "https://github.com/owner/repo/pull/99"
    call_log: list[tuple] = []

    def gh_run_spy(args, env=None):  # noqa: ARG001
        call_log.append(("gh", tuple(args)))
        if args[:2] == ["pr", "create"]:
            return _stub_gh_ok(pr_url)
        return _stub_gh_ok("")

    def subprocess_run_spy(args, **kwargs):  # noqa: ARG001
        call_log.append(("sub", tuple(args)))
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with patch("agent.verdict_execution.gh_run", side_effect=gh_run_spy), \
         patch("agent.verdict_execution.subprocess.run", side_effect=subprocess_run_spy):
        result = execute_approve_integration(
            verdict,
            workspace=workspace,
            run_id="run-20260514T100000Z",
            dev_branch="dev",
        )

    assert result.success is True
    assert result.pr_url == pr_url
    # Telemetry: alias preserves the verdict label
    assert result.verdict == "APPROVE_INTEGRATION"

    # Order: git push, gh pr create (no --draft), gh pr merge --auto --squash, issue comment, issue close.
    kinds = [c[0] for c in call_log]
    assert kinds[:4] == ["sub", "gh", "gh", "gh"], call_log

    # 1) git push -u origin <branch>
    assert call_log[0][1][:5] == ("git", "push", "-u", "origin", "autonomous/issue-42")

    # 2) gh pr create — base = verdict.base_branch ("dev"), no --draft
    create_args = call_log[1][1]
    assert create_args[:2] == ("pr", "create")
    assert "--base" in create_args
    assert create_args[create_args.index("--base") + 1] == "dev"
    assert "--draft" not in create_args
    assert "--head" in create_args
    assert create_args[create_args.index("--head") + 1] == "autonomous/issue-42"

    # 3) gh pr merge --auto --squash <pr_url>
    merge_args = call_log[2][1]
    assert merge_args[:4] == ("pr", "merge", "--auto", "--squash")
    assert pr_url in merge_args

    # 4) issue comment
    comment_args = call_log[3][1]
    assert comment_args[:2] == ("issue", "comment")
    assert "42" in comment_args


def test_execute_approve_integration_is_alias_for_approve(tmp_path):
    """Collapse #475: the alias must produce the same actions as APPROVE
    and only patch the verdict label. No more "degrade to APPROVE on
    missing dev_branch" branch — APPROVE itself is now the single path."""
    from agent.verdict_execution import (
        execute_approve_integration, Verdict, ExecutionResult,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=7,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-7",
        base_branch="claude-agent-station",
        reasoning="ok",
    )

    base_result = ExecutionResult(
        verdict="APPROVE",
        project=verdict.project,
        issue_number=7,
        success=True,
        pr_url="https://github.com/owner/repo/pull/12",
        actions=["git push", "gh pr create", "gh pr merge --auto --squash"],
    )

    with patch(
        "agent.verdict_execution.execute_approve",
        return_value=base_result,
    ) as approve_mock:
        result = execute_approve_integration(
            verdict, workspace=workspace, run_id="r1", dev_branch=None,
        )

    # Delegated to execute_approve once.
    approve_mock.assert_called_once()
    # All other fields preserved from the delegate's return.
    assert result.success is True
    assert result.pr_url == "https://github.com/owner/repo/pull/12"
    assert "gh pr merge --auto --squash" in result.actions
    # Only the verdict label is patched.
    assert result.verdict == "APPROVE_INTEGRATION"


def test_execute_approve_integration_push_failure_short_circuits(tmp_path):
    """If ``git push`` fails, no PR is opened and no auto-merge is armed."""
    from agent.verdict_execution import execute_approve_integration, Verdict

    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=99,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-99",
        base_branch="main",
    )

    push_fail = MagicMock()
    push_fail.returncode = 1
    push_fail.stderr = "remote rejected"
    push_fail.stdout = ""

    gh_calls: list[tuple] = []

    def gh_run_spy(args, env=None):  # noqa: ARG001
        gh_calls.append(tuple(args))
        return _stub_gh_ok("")

    with patch("agent.verdict_execution.subprocess.run", return_value=push_fail), \
         patch("agent.verdict_execution.gh_run", side_effect=gh_run_spy):
        result = execute_approve_integration(
            verdict, workspace=workspace, dev_branch="dev",
        )

    assert result.success is False
    assert result.error is not None
    assert "remote rejected" in result.error
    assert gh_calls == [], "no gh calls expected after push failure"


def test_execute_dispatcher_routes_approve_integration(tmp_path):
    """The ``execute`` dispatcher must route APPROVE_INTEGRATION correctly."""
    from agent.verdict_execution import execute, Verdict, ExecutionResult, _EXECUTORS

    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=1,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-1",
    )
    mock_fn = MagicMock()
    mock_fn.return_value = ExecutionResult(
        verdict="APPROVE_INTEGRATION",
        project=verdict.project,
        issue_number=1,
        success=True,
    )
    with patch.dict(_EXECUTORS, {"APPROVE_INTEGRATION": mock_fn}):
        execute(verdict, workspace=workspace, dev_branch="dev")
    mock_fn.assert_called_once()


def test_execute_dispatcher_forwards_dev_branch_kwarg(tmp_path):
    """The dispatcher must thread ``dev_branch`` through to the executor.

    Pins the contract so future Python callers (post-#383 bash deletion)
    know the kwarg name to pass.
    """
    from agent.verdict_execution import execute, Verdict, ExecutionResult, _EXECUTORS

    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=1,
        verdict="APPROVE_INTEGRATION",
        branch="autonomous/issue-1",
    )
    mock_fn = MagicMock()
    mock_fn.return_value = ExecutionResult(
        verdict="APPROVE_INTEGRATION",
        project="owner/repo",
        issue_number=1,
        success=True,
    )
    with patch.dict(_EXECUTORS, {"APPROVE_INTEGRATION": mock_fn}):
        execute(verdict, workspace=workspace, dev_branch="dev")
    _, kwargs = mock_fn.call_args
    assert kwargs.get("dev_branch") == "dev"


@pytest.mark.parametrize("verdict_kind", ["APPROVE", "PR", "REJECT", "SKIP"])
def test_every_executor_accepts_dev_branch_kwarg(verdict_kind, tmp_path, monkeypatch):
    """Regression: ``execute()`` dispatches via blind ``**kwargs``, so
    EVERY executor must accept ``dev_branch`` (only execute_approve_integration
    actually consumes it). Without this, an APPROVE verdict raises
    TypeError('execute_approve() got an unexpected keyword argument
    "dev_branch"'), the verdict-execution wrapper catches and bumps
    exit_code, the run is marked failed, and no PR ever opens.

    Live evidence: run-20260516T090429Z, digest line:
        execute_verdict: TypeError: execute_approve() got an
        unexpected keyword argument 'dev_branch'

    Caller (agent/project_loop.py) passes dev_branch to every verdict
    uniformly because the dispatcher demands it for APPROVE_INTEGRATION;
    making every executor accept-and-ignore is the kw-symmetry approach.
    """
    from unittest.mock import patch
    from agent.verdict_execution import execute, Verdict
    # Stub out the per-executor side effects so we just exercise signatures.
    monkeypatch.setattr("agent.verdict_execution.subprocess.run",
                        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("agent.verdict_execution.gh_run",
                        lambda *a, **kw: type("R", (), {"ok": True, "stdout": "", "stderr": ""})())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = Verdict(
        project="owner/repo",
        issue_number=42,
        verdict=verdict_kind,
        branch="autonomous/issue-42",
    )
    # MUST NOT raise TypeError
    result = execute(
        verdict,
        workspace=workspace,
        run_id="run-test",
        env={"GH_TOKEN": "test"},
        dev_branch="autonomous/dev",
    )
    # Whatever the executor returns, the call itself was valid.
    assert result is not None, f"{verdict_kind} executor returned None"


# --- #460: auto-close issue resolution ---


def test_resolve_issue_numbers_from_multi_issue_branch():
    """Branch like 'feature/backend-issues-29-30-...' yields [29, 30],
    deduplicated and sorted. Verdict.issue_number is unioned."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _verdict(
        branch="feature/backend-issues-29-30-20260519T080446Z",
        issue_number=30,
    )
    assert _resolve_issue_numbers(v) == [29, 30]


def test_resolve_issue_numbers_from_old_convention():
    """Branch like 'autonomous/issue-31' with matching verdict number
    yields [31] (deduped)."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _verdict(
        branch="autonomous/issue-31",
        issue_number=31,
    )
    assert _resolve_issue_numbers(v) == [31]


def test_resolve_issue_numbers_falls_back_to_verdict_only():
    """Branch with no number pattern yields just verdict.issue_number."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _verdict(
        branch="feature/no-numbers-here",
        issue_number=42,
    )
    assert _resolve_issue_numbers(v) == [42]


def test_resolve_issue_numbers_empty_when_no_source():
    """No branch match AND verdict.issue_number is None → []."""
    from agent.verdict_execution import _resolve_issue_numbers
    v = _verdict(
        branch="feature/no-numbers-here",
        issue_number=None,
    )
    assert _resolve_issue_numbers(v) == []


# --- #460: _close_issues integration ---


def test_execute_approve_closes_issue_after_pr_created(tmp_path):
    """APPROVE verdict triggers `gh issue close` after PR creation."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="APPROVE",
                 branch="autonomous/issue-42",
                 issue_number=42)
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        result = execute(v, workspace=tmp_path, run_id="run-test")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:3] == ["issue", "close", "42"]
    ]
    assert close_calls, f"Expected `gh issue close 42`, got: {[c.args[0] for c in mock_gh.call_args_list]}"
    assert result.success is True


def test_execute_approve_integration_closes_after_merge_armed(tmp_path):
    """APPROVE_INTEGRATION verdict also triggers `gh issue close`."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="APPROVE_INTEGRATION",
                 branch="autonomous/issue-42",
                 issue_number=42)
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        result = execute(v, workspace=tmp_path, run_id="run-test",
                         dev_branch="autonomous/dev")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:3] == ["issue", "close", "42"]
    ]
    assert close_calls, "Expected `gh issue close 42` on APPROVE_INTEGRATION"
    assert result.success is True


def test_close_issues_handles_multi_issue_branch(tmp_path):
    """A branch addressing multiple issues closes ALL of them."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="APPROVE",
                 branch="feature/backend-issues-29-30-20260519T080446Z",
                 issue_number=30)
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run") as mock_gh:
        mock_gh.return_value = _ok_gh_result(stdout="https://github.com/x/y/pull/1")
        execute(v, workspace=tmp_path, run_id="run-test")
    issue_numbers_closed = sorted({
        call.args[0][2]
        for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    })
    assert issue_numbers_closed == ["29", "30"], (
        f"Expected both 29 and 30 closed, got: {issue_numbers_closed}"
    )


def test_execute_reject_does_not_close_issue(tmp_path):
    """REJECT verdict must NOT close the issue."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="REJECT",
                 branch="autonomous/issue-42",
                 issue_number=42)
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        execute(v, workspace=tmp_path, run_id="run-test")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    ]
    assert not close_calls, (
        f"REJECT must not close issues, got close calls: {close_calls}"
    )


def test_execute_skip_does_not_close_issue(tmp_path):
    """SKIP verdict must NOT close the issue."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="SKIP",
                 branch="autonomous/issue-42",
                 issue_number=42)
    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               return_value=_ok_gh_result()) as mock_gh:
        execute(v, workspace=tmp_path, run_id="run-test")
    close_calls = [
        call for call in mock_gh.call_args_list
        if call.args and call.args[0][:2] == ["issue", "close"]
    ]
    assert not close_calls, "SKIP must not close issues"


def test_close_issues_swallows_gh_failure(tmp_path):
    """If `gh issue close` fails (e.g. already-closed), verdict still
    succeeds and a WARNING is logged."""
    from agent.verdict_execution import execute
    v = _verdict(verdict_kind="APPROVE",
                 branch="autonomous/issue-42",
                 issue_number=42)

    def gh_side_effect(args, env=None):
        if args[:2] == ["issue", "close"]:
            return _fail_gh_result(stderr="error: issue is already closed")
        return _ok_gh_result(stdout="https://github.com/x/y/pull/1")

    with patch("agent.verdict_execution.subprocess.run",
               return_value=_ok_subprocess()), \
         patch("agent.verdict_execution.gh_run",
               side_effect=gh_side_effect):
        result = execute(v, workspace=tmp_path, run_id="run-test")

    assert result.success is True
