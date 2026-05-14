"""Workspace setup: clone/refresh/prune a project repo.

Python port of agent/scripts/run-manager.sh::setup_workspace (issue #383).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    """Raised when workspace setup fails."""


def _slug(name: str) -> str:
    """owner/repo -> owner__repo (filesystem-safe)."""
    return name.replace("/", "__")


def ensure_workspace(project: dict, workspaces_dir: str) -> str:
    """Clone or refresh the project workspace. Returns the absolute path."""
    repo = project["name"]
    base = project.get("base_branch", "main")
    ws_root = Path(workspaces_dir)
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace = ws_root / _slug(repo)

    if not workspace.exists():
        logger.info("workspace: cloning %s -> %s", repo, workspace)
        url = f"https://github.com/{repo}.git"
        result = subprocess.run(
            ["git", "clone", url, str(workspace)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise WorkspaceError(f"clone failed: {result.stderr.strip()}")
    else:
        logger.info("workspace: refreshing %s", workspace)
        for cmd in (
            ["git", "fetch", "--all", "--prune"],
            ["git", "checkout", base],
            ["git", "pull", "--ff-only"],
        ):
            result = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning("workspace: %s exited %d: %s",
                               " ".join(cmd), result.returncode, result.stderr.strip())

    # Prune stale worktrees from prior runs.
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(workspace), capture_output=True, text=True,
    )
    return str(workspace)
