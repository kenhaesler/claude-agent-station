"""Tests for :func:`agent.station_orchestrator.ensure_integration_base_on_origin`.

Uses a real bare repo + clone (no network) to verify the branch actually
lands on ``origin`` — mocking subprocess here would only re-spec what we
already broke once. Reproduces the 2026-05-21 LCM bug where the
integration base branch was created locally but never pushed, causing
every ``gh pr create --base <base_branch>`` to 404.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def repo_pair(tmp_path: Path):
    """Create a bare ``origin`` repo plus a working clone with one commit
    on ``main``. Returns (origin_path, workspace_path)."""
    origin = tmp_path / "origin.git"
    workspace = tmp_path / "workspace"

    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))

    workspace.mkdir()
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.email", "t@t")
    _git(workspace, "config", "user.name", "t")
    (workspace / "README.md").write_text("hi\n")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-m", "init")
    _git(workspace, "remote", "add", "origin", str(origin))
    _git(workspace, "push", "-u", "origin", "main")

    return origin, workspace


def _remote_branches(origin: Path) -> set[str]:
    out = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_creates_and_pushes_base_branch_when_missing_on_origin(repo_pair):
    """The 2026-05-21 regression case: ``claude-agent-station`` exists
    locally only. After the helper runs, it must be on origin too."""
    from agent.station_orchestrator import ensure_integration_base_on_origin

    origin, workspace = repo_pair
    assert "claude-agent-station" not in _remote_branches(origin)

    ensure_integration_base_on_origin(str(workspace), "claude-agent-station")

    assert "claude-agent-station" in _remote_branches(origin), (
        "integration base branch must be pushed to origin so PRs can target it"
    )


def test_pulls_existing_base_branch_without_force_push(repo_pair):
    """When the branch already exists on origin, the helper must check
    out and pull — not re-create or force-push."""
    from agent.station_orchestrator import ensure_integration_base_on_origin

    origin, workspace = repo_pair
    # Seed an existing remote branch with an extra commit.
    _git(workspace, "checkout", "-b", "autonomous/dev")
    (workspace / "extra.txt").write_text("x\n")
    _git(workspace, "add", "extra.txt")
    _git(workspace, "commit", "-m", "extra")
    _git(workspace, "push", "-u", "origin", "autonomous/dev")
    upstream_sha = _git(workspace, "rev-parse", "autonomous/dev").stdout.strip()

    # Drop the local branch so the helper has to pull from origin.
    _git(workspace, "checkout", "main")
    _git(workspace, "branch", "-D", "autonomous/dev")

    ensure_integration_base_on_origin(str(workspace), "autonomous/dev")

    # Local now matches origin and origin is untouched.
    local_sha = _git(workspace, "rev-parse", "autonomous/dev").stdout.strip()
    assert local_sha == upstream_sha
    origin_sha = _git(origin, "rev-parse", "autonomous/dev").stdout.strip()
    assert origin_sha == upstream_sha


def test_push_failure_is_logged_not_raised(repo_pair, caplog, monkeypatch):
    """If origin rejects the push (e.g. branch protection), the helper
    must warn and return — never raise into the orchestrator."""
    from agent import station_orchestrator as orch

    _, workspace = repo_pair

    # Simulate push rejection by pointing the remote at a path that
    # doesn't accept writes.
    _git(workspace, "remote", "set-url", "origin", "/nonexistent-path.git")

    with caplog.at_level("WARNING", logger="agent.station_orchestrator"):
        orch.ensure_integration_base_on_origin(
            str(workspace), "claude-agent-station",
        )

    assert any(
        "could not push integration base branch" in rec.message
        for rec in caplog.records
    ), "push failure must be surfaced as a WARNING"
