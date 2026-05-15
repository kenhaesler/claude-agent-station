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


def test_clone_url_embeds_gh_token_when_present(monkeypatch):
    """Without an embedded credential the bare HTTPS URL blocks waiting
    for stdin auth inside the runner container — first live triggered
    run aborted with ``could not read Username for 'https://github.com'``.
    The runner has ``GH_TOKEN`` in its env (set by the launcher); embed
    it as the user portion of the URL.
    """
    from agent.workspace_setup import _clone_url

    monkeypatch.setenv("GH_TOKEN", "ghs_dummy_token_xyz")
    url = _clone_url("owner/repo")
    assert url.startswith("https://x-access-token:ghs_dummy_token_xyz@")
    assert "github.com/owner/repo.git" in url


def test_clone_url_falls_back_to_bare_https_when_no_token(monkeypatch):
    """No token → no embedding. Operators running the orchestrator
    outside the launcher (manual debugging) get a clean recognisable
    URL and a clean recognisable failure mode.
    """
    from agent.workspace_setup import _clone_url

    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert _clone_url("owner/repo") == "https://github.com/owner/repo.git"


def test_clone_url_treats_empty_token_as_missing(monkeypatch):
    """An empty ``GH_TOKEN`` (e.g. ``STATION_GH_TOKEN=""`` in .env) must
    not produce a malformed URL like ``https://x-access-token:@github.com/``.
    """
    from agent.workspace_setup import _clone_url

    monkeypatch.setenv("GH_TOKEN", "   ")
    assert _clone_url("owner/repo") == "https://github.com/owner/repo.git"


def test_clone_failure_redacts_token_from_stderr(tmp_path, monkeypatch):
    """If ``git clone`` fails the URL containing the token MUST be
    scrubbed before reaching :class:`WorkspaceError`. Otherwise the
    operator's screen / logs carry the short-lived but live App token
    in plaintext.
    """
    from agent.workspace_setup import ensure_workspace, WorkspaceError

    monkeypatch.setenv("GH_TOKEN", "ghs_sensitive_token")
    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)

    def _fail_clone(cmd, *a, **kw):
        return subprocess.CompletedProcess(
            args=cmd, returncode=128, stdout="",
            stderr=("fatal: unable to access "
                    "'https://x-access-token:ghs_sensitive_token@github.com/o/r.git/'"),
        )
    monkeypatch.setattr(
        "agent.workspace_setup.subprocess.run", MagicMock(side_effect=_fail_clone),
    )

    with pytest.raises(WorkspaceError) as exc_info:
        ensure_workspace({"repo": "o/r"}, str(tmp_path))

    msg = str(exc_info.value)
    assert "ghs_sensitive_token" not in msg, "token leaked into WorkspaceError"
    assert "x-access-token" not in msg
    assert "github.com/o/r.git" in msg  # the redacted URL is still informative


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
