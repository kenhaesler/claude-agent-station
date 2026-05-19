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


# --- #462: stale-branch cleanup ---


def _build_git_branch_dispatch(branches_output: str, issue_states: dict):
    """Build a subprocess.run side_effect that:
      - Returns ``branches_output`` for ``git branch --format=...``.
      - Returns ``issue_states[N]`` JSON for ``gh issue view N ...`` calls.
      - Returns a no-op success for everything else (git clone, fetch, checkout,
        pull, worktree prune, git branch -D).

    ``issue_states`` is keyed by issue number (int) with values either
    'OPEN', 'CLOSED', or None (None simulates a gh query failure).
    """
    deleted_branches = []

    def dispatch(cmd, *args, **kwargs):
        # git branch list
        if isinstance(cmd, list) and cmd[:2] == ["git", "branch"] and "--format=%(refname:short)" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=branches_output, stderr="")
        # gh issue view N --json state -q .state
        if isinstance(cmd, list) and cmd[:3] == ["gh", "issue", "view"]:
            n = int(cmd[3])
            state = issue_states.get(n)
            if state is None:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=state, stderr="")
        # git branch -D <branch>
        if isinstance(cmd, list) and cmd[:3] == ["git", "branch", "-D"]:
            deleted_branches.append(cmd[3])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # Default: success no-op (clone/fetch/checkout/pull/worktree-prune)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    dispatch.deleted_branches = deleted_branches  # type: ignore[attr-defined]
    return dispatch


def test_prune_keeps_branch_with_no_issue_numbers(tmp_path, monkeypatch):
    """A branch whose name parses to no issue numbers must be preserved."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\nfeat/no-numbers-here\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feat/no-numbers-here" not in dispatch.deleted_branches, (
        f"Branch with no issue numbers must be preserved; deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_integration_branch_explicitly(tmp_path, monkeypatch):
    """`claude-agent-station` must never be deleted regardless of issue states."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "claude-agent-station" not in dispatch.deleted_branches


def test_prune_keeps_base_branch_explicitly(tmp_path, monkeypatch):
    """The project's base branch (e.g. `main`) must never be deleted."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = "claude-agent-station\nmain\n"
    dispatch = _build_git_branch_dispatch(branches, issue_states={})
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "main" not in dispatch.deleted_branches


def test_prune_deletes_branch_when_all_referenced_issues_closed(tmp_path, monkeypatch):
    """Branch `feature/backend-issues-29-30-...` where both #29 and #30 are CLOSED must be deleted."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "feature/backend-issues-29-30-20260519T080446Z\n"
    )
    issue_states = {29: "CLOSED", 30: "CLOSED"}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feature/backend-issues-29-30-20260519T080446Z" in dispatch.deleted_branches, (
        f"Branch with all CLOSED issues should be deleted; deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_branch_when_any_referenced_issue_still_open(tmp_path, monkeypatch):
    """Branch `feature/backend-issues-29-61-...` where #29 is CLOSED but #61 is OPEN must be preserved."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "feature/backend-issues-29-61-20260519T080446Z\n"
    )
    issue_states = {29: "CLOSED", 61: "OPEN"}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "feature/backend-issues-29-61-20260519T080446Z" not in dispatch.deleted_branches, (
        f"Branch with at least one OPEN referenced issue must be preserved; "
        f"deleted={dispatch.deleted_branches}"
    )


def test_prune_keeps_branch_when_github_query_fails(tmp_path, monkeypatch):
    """If `gh issue view N` fails for a branch's issue, the branch must be preserved (fail-soft + conservative)."""
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    branches = (
        "claude-agent-station\n"
        "main\n"
        "autonomous/issue-99\n"
    )
    # issue 99 is intentionally NOT in the cache → query "fails"
    issue_states = {}
    dispatch = _build_git_branch_dispatch(branches, issue_states=issue_states)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=dispatch))

    ensure_workspace({"repo": "owner/repo", "branch": "main"}, str(tmp_path))

    assert "autonomous/issue-99" not in dispatch.deleted_branches, (
        f"Branch must be preserved when issue state query fails; deleted={dispatch.deleted_branches}"
    )
