"""Workspace setup: clone/refresh/prune a project repo.

Python port of agent/scripts/run-manager.sh::setup_workspace (issue #383).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from agent.verdict_execution import _issue_numbers_from_branch

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


def _prune_stale_branches(
    workspace: Path,
    project_repo: str,
    base_branch: str,
    env: dict[str, str] | None = None,
) -> None:
    """Delete local branches whose referenced GitHub issues are all CLOSED.

    Best-effort. Failures (git command failures, gh query errors) are
    logged at WARNING and the function continues. The integration
    branch (``claude-agent-station``) and ``base_branch`` are NEVER
    deleted, regardless of their names.

    Issue numbers are parsed from branch names via the regex defined
    in :data:`agent.verdict_execution._BRANCH_ISSUES_RE` (added by
    PR #461) — single source of truth across the codebase.

    Conservative: when an issue's state can't be determined, branches
    referencing it are preserved. Only deletes when ALL referenced
    issues are confirmed CLOSED.

    #462.
    """
    PRESERVED = {"claude-agent-station", base_branch}

    # Step 1: list local branches.
    list_result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if list_result.returncode != 0:
        logger.warning("prune: git branch list failed: %s",
                       list_result.stderr.strip())
        return
    branches = [b.strip() for b in list_result.stdout.splitlines() if b.strip()]

    # Step 2: parse issue numbers per branch (skipping preserved names).
    branch_issues: dict[str, list[int]] = {}
    for b in branches:
        if b in PRESERVED:
            continue
        numbers = _issue_numbers_from_branch(b)
        if numbers:
            branch_issues[b] = numbers

    if not branch_issues:
        return

    # Step 3: query unique issues once, build cache.
    all_numbers = {n for nums in branch_issues.values() for n in nums}
    issue_states: dict[int, str | None] = {}
    for n in sorted(all_numbers):
        result = subprocess.run(
            ["gh", "issue", "view", str(n), "--repo", project_repo,
             "--json", "state", "-q", ".state"],
            cwd=str(workspace), capture_output=True, text=True, env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            issue_states[n] = result.stdout.strip()
        else:
            issue_states[n] = None  # unknown — branch will be preserved
            logger.warning("prune: gh issue view %s failed: %s",
                           n, result.stderr.strip()[:200])

    # Step 4: delete branches where ALL referenced issues are CLOSED.
    for branch, numbers in branch_issues.items():
        states = [issue_states.get(n) for n in numbers]
        if all(s == "CLOSED" for s in states):
            del_result = subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(workspace), capture_output=True, text=True, env=env,
            )
            if del_result.returncode == 0:
                logger.info("prune: deleted stale branch %s (issues %s all CLOSED)",
                            branch, numbers)
            else:
                logger.warning("prune: git branch -D %s failed: %s",
                               branch, del_result.stderr.strip()[:200])


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

    # #462: prune stale local branches referencing closed issues.
    # Fail-soft — never blocks the run.
    try:
        _prune_stale_branches(workspace, repo, base, env=None)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.exception("prune: _prune_stale_branches failed (non-fatal)")

    return str(workspace)
