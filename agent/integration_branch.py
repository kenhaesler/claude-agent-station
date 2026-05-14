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


def _git_or_raise(cwd: str, *args: str, op: str) -> subprocess.CompletedProcess:
    """Run git; raise IntegrationBranchError on non-zero return code.

    Every prerequisite step in :func:`merge_to_dev` must succeed before
    we merge — silently continuing past a failed ``pull --ff-only`` (the
    literal scenario this function exists to handle) would produce a
    merge against stale state.
    """
    r = _git(cwd, *args)
    if r.returncode != 0:
        raise IntegrationBranchError(
            f"{op} failed (rc={r.returncode}): {r.stderr.strip()[:400]}"
        )
    return r


def _branch_exists(cwd: str, name: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", f"refs/heads/{name}").returncode == 0


def merge_to_dev(
    *, project: str, feature_branch: str, base_branch: str,
    issue_number: int, reasoning: str, workspaces_dir: str,
) -> None:
    """Push the feature branch and merge it into the integration branch.

    Every git step is checked. The only step that retries on failure is
    the initial ``push origin <feature_branch>`` (which can race against
    a concurrent push by another runner); every other step fails the
    whole operation rather than continuing against undefined state.
    """
    workspace = Path(workspaces_dir) / _slug(project)
    cwd = str(workspace)

    # Push the feature branch with retry on race against concurrent pushes.
    for attempt in range(1, _PUSH_MAX_RETRIES + 1):
        r = _git(cwd, "push", "origin", feature_branch)
        if r.returncode == 0:
            break
        logger.warning("integration: push attempt %d failed: %s", attempt, r.stderr.strip())
        if attempt == _PUSH_MAX_RETRIES:
            raise IntegrationBranchError(f"push failed after {attempt} attempts: {r.stderr.strip()}")
        # Try a fetch before retrying. Don't raise on fetch failure here —
        # the retry of push will surface any persistent connectivity issue
        # with a clearer error message.
        _git(cwd, "fetch", "origin", feature_branch)

    # Bootstrap dev if missing — every step is checked so a bootstrap
    # failure doesn't leak into the merge phase as a confusing "merge
    # failed: no such branch".
    if not _branch_exists(cwd, _INTEGRATION_BRANCH):
        logger.info("integration: bootstrapping %s from %s", _INTEGRATION_BRANCH, base_branch)
        _git_or_raise(cwd, "checkout", base_branch, op="bootstrap checkout base")
        _git_or_raise(cwd, "checkout", "-b", _INTEGRATION_BRANCH, op="bootstrap create dev")
        _git_or_raise(cwd, "push", "-u", "origin", _INTEGRATION_BRANCH, op="bootstrap push dev")

    # Checkout dev, pull (must be ff-only — anything else means dev has
    # diverged and we'd be merging against stale state). The merge can
    # only proceed after pull confirms we're at origin's tip.
    _git_or_raise(cwd, "checkout", _INTEGRATION_BRANCH, op="checkout dev")
    _git_or_raise(cwd, "pull", "--ff-only", op="pull dev --ff-only")

    msg = f"Merge {feature_branch} (issue #{issue_number}): {reasoning[:200]}"
    _git_or_raise(cwd, "merge", "--no-ff", "-m", msg, feature_branch, op="merge feature")
    _git_or_raise(cwd, "push", "origin", _INTEGRATION_BRANCH, op="push dev")
    logger.info("integration: merged %s into %s", feature_branch, _INTEGRATION_BRANCH)
