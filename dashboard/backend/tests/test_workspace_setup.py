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

    project = {"repo": "owner/repo", "branch": "main"}
    path = ensure_workspace(project, str(tmp_path))

    # Should have called git clone at least once.
    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    assert any("clone" in str(c) for c in calls), "ensure_workspace must clone when path is missing"
    assert "owner/repo" in path or "repo" in path


def test_refresh_existing(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    project = {"repo": "owner/repo", "branch": "main"}
    ensure_workspace(project, str(tmp_path))

    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    cmd_strs = [str(c) for c in calls]
    assert any("fetch" in s for s in cmd_strs), "must git fetch on existing workspace"
    assert any("checkout" in s for s in cmd_strs), "must git checkout the base branch"


def test_worktree_prune_runs(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    ensure_workspace({"repo": "owner/repo"}, str(tmp_path))

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
        ensure_workspace({"repo": "owner/bad"}, str(tmp_path))


def test_rejects_legacy_name_field_loudly(tmp_path, monkeypatch):
    """The bash port (#383) originally read ``project["name"]`` and
    ``project.get("base_branch")``, but ``manager-config.json`` (written
    by the dashboard's config_sync) uses ``repo`` / ``branch``. The
    first live triggered run after the post-#386 stack rebuild aborted
    with ``KeyError: 'name'``. Pin the contract on the canonical names.
    """
    from agent.workspace_setup import ensure_workspace

    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))
    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)

    # Legacy field name MUST NOT silently work — without ``repo`` we
    # expect KeyError, not a silent fall-through that produces an
    # empty repo path.
    with pytest.raises(KeyError, match="repo"):
        ensure_workspace({"name": "owner/legacy"}, str(tmp_path))


def test_branch_field_is_canonical(tmp_path, monkeypatch):
    """``branch`` is the field the dashboard writes (matches the
    SQLAlchemy ``Project.branch`` column). ``base_branch`` was the
    bash variable name; ignoring it silently let runs check out
    ``main`` instead of the project's configured branch.
    """
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__rep").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    ensure_workspace({"repo": "owner/rep", "branch": "develop"}, str(tmp_path))
    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    cmd_strs = [str(c) for c in calls]
    assert any("checkout" in s and "develop" in s for s in cmd_strs), (
        "ensure_workspace must read ``branch``, not the legacy ``base_branch``"
    )
