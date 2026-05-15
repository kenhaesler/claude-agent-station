"""Workspace setup: clone/refresh/prune a project repo.

Python port of agent/scripts/run-manager.sh::setup_workspace (issue #383).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _clone_url(repo: str) -> str:
    """Build the HTTPS clone URL, embedding ``GH_TOKEN`` when set.

    There's no git credential helper configured inside the runner
    container, so a bare ``https://github.com/owner/repo.git`` URL
    blocks waiting for stdin credentials — which is why the first live
    triggered run aborted with ``could not read Username for
    'https://github.com'``.

    Embedding ``x-access-token:<token>@`` lets git use the GitHub App
    installation token the launcher fetches at spawn time without
    needing ``gh auth setup-git`` to have been run inside the image.
    The token is operator-level and short-lived (App tokens are ~1 hour
    TTL); leaking it into a clone URL command line is acceptable
    because the runner container itself is the only environment that
    sees it.

    Falls back to the bare URL when no token is available so an
    operator running the orchestrator outside the launcher (manual
    debugging, local dev) still gets a clean failure rather than a
    surprise behaviour change.
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        return f"https://github.com/{repo}.git"
    return f"https://x-access-token:{token}@github.com/{repo}.git"


class WorkspaceError(RuntimeError):
    """Raised when workspace setup fails."""


def _slug(name: str) -> str:
    """owner/repo -> owner__repo (filesystem-safe)."""
    return name.replace("/", "__")


def ensure_workspace(project: dict, workspaces_dir: str) -> str:
    """Clone or refresh the project workspace. Returns the absolute path.

    The ``project`` dict is read from ``manager-config.json`` (written by
    the dashboard's ``services/config_sync.py``) and follows the same
    field names as the ``Project`` SQLAlchemy model: ``repo`` for
    ``owner/name`` and ``branch`` for the base branch. The bash→Python
    port (#383) accidentally carried over the bash variable names
    ``name`` / ``base_branch``; SQLite-only tests didn't surface the
    mismatch and the first live triggered run after the post-#386 stack
    rebuild blew up with ``KeyError: 'name'``.
    """
    repo = project["repo"]
    base = project.get("branch", "main")
    ws_root = Path(workspaces_dir)
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace = ws_root / _slug(repo)

    if not workspace.exists():
        logger.info("workspace: cloning %s -> %s", repo, workspace)
        url = _clone_url(repo)
        result = subprocess.run(
            ["git", "clone", url, str(workspace)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Redact the token from the URL before raising — the
            # stderr buffer is the load-bearing diagnostic operators
            # see, and "clone failed: fatal: ... https://x-access-token:
            # ghs_xyz@github.com/..." is a bad day for whoever's
            # screen the error lands on.
            stderr = result.stderr.strip().replace(url, f"https://github.com/{repo}.git")
            raise WorkspaceError(f"clone failed: {stderr}")
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
