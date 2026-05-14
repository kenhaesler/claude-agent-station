"""Integration-branch merge: push feature -> merge into dev.

Python port of agent/scripts/integration-branch.sh::merge_to_dev (issue #383).
The bash file remains for ad-hoc cron / dashboard callers.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class IntegrationBranchError(RuntimeError):
    """Raised when push/merge fails irrecoverably."""


_INTEGRATION_BRANCH = "dev"
_PUSH_MAX_RETRIES = 3


def _slug(name: str) -> str:
    return name.replace("/", "__")


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _branch_exists(cwd: str, name: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", f"refs/heads/{name}").returncode == 0


def merge_to_dev(
    *, project: str, feature_branch: str, base_branch: str,
    issue_number: int, reasoning: str, workspaces_dir: str,
) -> None:
    """Push the feature branch and merge it into the integration branch."""
    workspace = Path(workspaces_dir) / _slug(project)
    cwd = str(workspace)

    # Push the feature branch with retry.
    for attempt in range(1, _PUSH_MAX_RETRIES + 1):
        r = _git(cwd, "push", "origin", feature_branch)
        if r.returncode == 0:
            break
        logger.warning("integration: push attempt %d failed: %s", attempt, r.stderr.strip())
        if attempt == _PUSH_MAX_RETRIES:
            raise IntegrationBranchError(f"push failed after {attempt} attempts: {r.stderr.strip()}")
        # Try a fetch + rebase before retrying.
        _git(cwd, "fetch", "origin", feature_branch)

    # Bootstrap dev if missing.
    if not _branch_exists(cwd, _INTEGRATION_BRANCH):
        logger.info("integration: bootstrapping %s from %s", _INTEGRATION_BRANCH, base_branch)
        _git(cwd, "checkout", base_branch)
        _git(cwd, "checkout", "-b", _INTEGRATION_BRANCH)
        _git(cwd, "push", "-u", "origin", _INTEGRATION_BRANCH)

    # Checkout dev, merge feature.
    _git(cwd, "checkout", _INTEGRATION_BRANCH)
    _git(cwd, "pull", "--ff-only")
    msg = f"Merge {feature_branch} (issue #{issue_number}): {reasoning[:200]}"
    r = _git(cwd, "merge", "--no-ff", "-m", msg, feature_branch)
    if r.returncode != 0:
        raise IntegrationBranchError(f"merge failed: {r.stderr.strip()}")

    r = _git(cwd, "push", "origin", _INTEGRATION_BRANCH)
    if r.returncode != 0:
        raise IntegrationBranchError(f"push of {_INTEGRATION_BRANCH} failed: {r.stderr.strip()}")
    logger.info("integration: merged %s into %s", feature_branch, _INTEGRATION_BRANCH)
