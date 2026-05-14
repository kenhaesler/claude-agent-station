"""Tests for agent.workspace_setup (issue #383 bash port)."""
from __future__ import annotations

import subprocess
import pytest
from unittest.mock import MagicMock, patch


def _git_ok(cmd, *a, **kw):
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_fresh_clone(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))
    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)

    project = {"name": "owner/repo", "base_branch": "main"}
    path = ensure_workspace(project, str(tmp_path))

    # Should have called git clone at least once.
    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    assert any("clone" in str(c) for c in calls), "ensure_workspace must clone when path is missing"
    assert "owner/repo" in path or "repo" in path


def test_refresh_existing(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    project = {"name": "owner/repo", "base_branch": "main"}
    ensure_workspace(project, str(tmp_path))

    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    cmd_strs = [str(c) for c in calls]
    assert any("fetch" in s for s in cmd_strs), "must git fetch on existing workspace"
    assert any("checkout" in s for s in cmd_strs), "must git checkout the base branch"


def test_worktree_prune_runs(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    ensure_workspace({"name": "owner/repo"}, str(tmp_path))

    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    assert any("worktree" in str(c) and "prune" in str(c) for c in calls), (
        "ensure_workspace must run `git worktree prune`"
    )


def test_bad_remote_raises(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace, WorkspaceError

    def _fail(cmd, *a, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="fatal: ...")

    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_fail))

    with pytest.raises(WorkspaceError, match="clone"):
        ensure_workspace({"name": "owner/bad"}, str(tmp_path))
