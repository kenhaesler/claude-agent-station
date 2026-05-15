"""Concrete ``gh`` adapter for the issue-splitter (#391).

PR-2 defined a small ``_GhClient`` Protocol in
:mod:`agent.issue_splitter.github_ops` so the splitter's GitHub side
effects (label ensure, issue create, parent comment) could be mocked at
the method level in tests. This module supplies the runtime
implementation by composing the existing :func:`agent.gh_client.gh_run`
and :func:`agent.gh_client.gh_json` helpers — there is no class-style
``GhClient`` upstream, just module-level functions, so this adapter is
the seam where method-style intent (``gh.create_issue(...)``) meets
argv-style invocation (``gh issue create --repo ... --title ...``).

The adapter also exposes the two extra methods PR-3 needs beyond the
Protocol: ``add_labels`` (used to tag the parent ``split`` after
execution) and ``ensure_branch`` (used by ``_ensure_integration_branch``
to create ``integration/issue-<N>`` off ``dev``). Both are implemented
on top of ``gh api`` calls so they hit the same auth/error path as the
rest of the splitter — no second SDK to maintain.

Why a class instead of more module-level functions: the test seam in
:mod:`agent.coordinator.decide` is ``_gh_client()``, a factory that
returns the adapter. Tests patch the factory and inject a ``MagicMock``,
so the call sites just have to talk to "the adapter" — they don't have
to import each function name individually.
"""
from __future__ import annotations

import logging
from typing import Sequence

from urllib.parse import quote

from agent.gh_client import GhError, gh_json, gh_run

logger = logging.getLogger(__name__)


class GhAdapter:
    """Method-style wrapper around the ``gh`` CLI.

    Method shape mirrors the ``_GhClient`` Protocol in
    :mod:`agent.issue_splitter.github_ops` plus the extra hooks PR-3
    needs (``add_labels`` / ``ensure_branch``). Each method composes a
    single ``gh`` argv and delegates to the central subprocess helper —
    no retry/auth logic lives here, that's the job of
    :func:`agent.gh_client.gh_run` / :func:`agent.gh_client.gh_json`.
    """

    # ----- Protocol methods (used by github_ops) ------------------------

    def label_exists(self, owner: str, repo: str, name: str) -> bool:
        """Return True if ``name`` exists on ``owner/repo``.

        Uses ``gh api repos/{owner}/{repo}/labels/{name}`` — a 200 means
        the label exists, a 404 means it doesn't. Any other error is
        propagated as :class:`GhError` (the caller decides whether to
        treat it as fatal or fall through).

        The label name is URL-encoded so labels containing ``/``, ``?``,
        or other reserved characters route correctly. Current callers
        only pass ``splitter-proposed``, but defending the helper at the
        boundary is cheaper than discovering a future label name breaks
        the path.
        """
        try:
            gh_json([
                "api",
                f"repos/{owner}/{repo}/labels/{quote(name, safe='')}",
            ])
            return True
        except GhError as exc:
            if "404" in exc.stderr or "Not Found" in exc.stderr:
                return False
            raise

    def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        *,
        color: str,
        description: str,
    ) -> None:
        result = gh_run([
            "label", "create", name,
            "--repo", f"{owner}/{repo}",
            "--color", color,
            "--description", description,
        ])
        if not result.ok:
            # ``gh label create`` exits 1 on already-exists; treat that as
            # idempotent success (the caller already gated via
            # ``label_exists`` but a race with another orchestrator is
            # possible).
            if "already exists" in result.stderr:
                logger.debug("label %s already exists on %s/%s", name, owner, repo)
                return
            raise GhError(result.cmd, result.returncode, result.stderr)

    def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: Sequence[str],
    ) -> dict:
        """Create an issue and return ``{"number": N, "url": URL}``.

        Returns a dict (not a full GitHub API payload) because the
        Protocol contract only requires ``["number"]`` and the URL is
        useful for log lines. ``gh issue create`` prints the URL to
        stdout; the number is parsed from it.
        """
        argv = [
            "issue", "create",
            "--repo", f"{owner}/{repo}",
            "--title", title,
            "--body", body,
        ]
        if labels:
            argv += ["--label", ",".join(labels)]
        result = gh_run(argv)
        if not result.ok:
            raise GhError(result.cmd, result.returncode, result.stderr)
        url = result.stdout.strip()
        number = int(url.rstrip("/").rsplit("/", 1)[1])
        return {"number": number, "url": url}

    def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        body: str,
    ) -> None:
        result = gh_run([
            "issue", "comment", str(issue_number),
            "--repo", f"{owner}/{repo}",
            "--body", body,
        ])
        if not result.ok:
            raise GhError(result.cmd, result.returncode, result.stderr)

    # ----- Extra methods (used by execute_split_decision) ---------------

    def add_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: Sequence[str],
    ) -> None:
        """Attach one or more labels to an existing issue.

        Emits one ``--add-label`` flag per label rather than a
        comma-joined value: the comma form is undocumented and has been
        version-dependent across ``gh`` releases, while the repeated-flag
        form is the documented contract and works on every supported
        version.
        """
        if not labels:
            return
        argv = ["issue", "edit", str(issue_number), "--repo", f"{owner}/{repo}"]
        for label in labels:
            argv += ["--add-label", label]
        result = gh_run(argv)
        if not result.ok:
            raise GhError(result.cmd, result.returncode, result.stderr)

    def ensure_branch(
        self,
        repo: str,
        branch: str,
        *,
        from_branch: str,
    ) -> None:
        """Create ``branch`` from ``from_branch`` if it doesn't exist.

        Idempotent: a 200 on the GET means we exit early; only the
        missing-branch case issues the POST to create a new ref.
        Errors other than "branch missing" propagate.
        """
        # 1. Check whether the target branch already exists.
        try:
            gh_json(["api", f"repos/{repo}/git/refs/heads/{branch}"])
            return
        except GhError as exc:
            if "404" not in exc.stderr and "Not Found" not in exc.stderr:
                raise
            # fall through and create the branch

        # 2. Look up the source branch's HEAD SHA.
        head = gh_json(["api", f"repos/{repo}/git/refs/heads/{from_branch}"])
        # ``gh api`` returns the raw GitHub payload — ref / object.sha.
        sha = head["object"]["sha"]

        # 3. Create the new ref.
        result = gh_run([
            "api", f"repos/{repo}/git/refs",
            "-X", "POST",
            "-f", f"ref=refs/heads/{branch}",
            "-f", f"sha={sha}",
        ])
        if not result.ok:
            raise GhError(result.cmd, result.returncode, result.stderr)
