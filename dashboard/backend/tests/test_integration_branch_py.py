"""Tests for agent.integration_branch (issue #383 port of merge_to_dev)."""
from __future__ import annotations

import subprocess
import pytest
from unittest.mock import MagicMock


def _ok():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_push_then_merge_ok(tmp_path, monkeypatch):
    from agent import integration_branch

    calls = []

    def _run(cmd, *a, **kw):
        calls.append(cmd)
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="autonomous/issue-1",
        base_branch="main", issue_number=1, reasoning="ok",
        workspaces_dir=str(tmp_path),
    )
    cmd_strs = [" ".join(c) for c in calls]
    assert any("push" in s for s in cmd_strs), "must push feature branch"
    assert any("merge" in s and "autonomous/issue-1" in s for s in cmd_strs), "must merge feature into dev"


def test_push_retry_after_initial_failure(tmp_path, monkeypatch):
    from agent import integration_branch

    state = {"attempts": 0}

    def _run(cmd, *a, **kw):
        if "push" in cmd:
            state["attempts"] += 1
            if state["attempts"] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="rejected")
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="b", base_branch="main",
        issue_number=2, reasoning="r", workspaces_dir=str(tmp_path),
    )
    assert state["attempts"] >= 2, "must retry push at least once on initial failure"


def test_merge_conflict_raises(tmp_path, monkeypatch):
    from agent import integration_branch

    def _run(cmd, *a, **kw):
        if "merge" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="CONFLICT (content)")
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    with pytest.raises(integration_branch.IntegrationBranchError, match="CONFLICT"):
        integration_branch.merge_to_dev(
            project="owner/repo", feature_branch="b", base_branch="main",
            issue_number=3, reasoning="r", workspaces_dir=str(tmp_path),
        )


def test_dev_bootstrap_on_missing(tmp_path, monkeypatch):
    """If the dev branch doesn't exist, the function creates it from base before merging."""
    from agent import integration_branch

    branches: set[str] = {"main"}

    def _run(cmd, *a, **kw):
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            ref = cmd[-1]
            return subprocess.CompletedProcess(args=cmd, returncode=0 if ref in branches else 1, stdout="", stderr="")
        if cmd[:2] == ["git", "checkout"] and "-b" in cmd:
            branches.add(cmd[-1])
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="b", base_branch="main",
        issue_number=4, reasoning="r", workspaces_dir=str(tmp_path),
    )
    assert "dev" in branches, "merge_to_dev must bootstrap the dev branch when absent"
