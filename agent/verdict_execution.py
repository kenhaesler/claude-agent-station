"""Execute manager verdicts via gh/git subprocess calls.

The manager review writes verdicts JSON like:

    {"verdicts": [
        {"project": "owner/repo",
         "issue_number": 123,
         "verdict": "APPROVE" | "PR" | "REJECT" | "SKIP",
         "branch": "autonomous/issue-123",
         "base_branch": "main",
         "reasoning": "Manager's prose explanation",
         "mode": "full"},
        ...
    ]}

The bash currently walks this list and executes each verdict inline
via ``gh pr create`` / ``git push`` / ``gh issue comment`` / label edits
(see ``agent/scripts/run-manager.sh`` lines ~2050–2500).

This module provides the **Python primitives** for those operations so
the bash deletion in a follow-up session has a stable target. Today it
is invokable but not yet wired — the bash continues to execute verdicts
directly. See :func:`execute` for the dispatcher signature.

What's intentionally NOT in this module:

- Analyze-mode handling, integration-branch merging, queue state
  updates, intelligence-loop outcome recording. Those live in
  ``run-manager.sh`` and stay there until the broader bash phase port
  lands (deferred follow-up).
- Conflict resolution. Continues to invoke the separate
  ``agent.conflict_resolver`` subprocess; this module does not duplicate
  that logic.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.gh_client import gh_run

logger = logging.getLogger(__name__)

# Path to the conflict-resolution harness (rebase → optional LLM →
# push). The harness assumes HEAD is on the feature branch already;
# :func:`_resolve_pr_conflict_if_needed` does the checkout before
# invoking it.
_RESOLVE_CONFLICTS_SCRIPT = (
    Path(__file__).resolve().parent / "scripts" / "resolve-conflicts.sh"
)

# Polling parameters for the post-PR-create mergeability check. GitHub
# computes ``mergeable`` asynchronously; freshly-created PRs spend a few
# seconds in UNKNOWN before settling. Two attempts at 3s spacing means
# at most 3s of added latency on the happy path (the first response is
# usually definitive once the PR has commits behind it) and worst-case
# 6s when the PR genuinely lands in UNKNOWN. Tunable via the module
# constants for test/operator overrides.
_PR_MERGEABILITY_POLL_ATTEMPTS = 2
_PR_MERGEABILITY_POLL_DELAY_S = 3.0

VerdictKind = Literal[
    "APPROVE",              # Direct merge to base (today's APPROVE)
    "APPROVE_INTEGRATION",  # Non-draft PR against integration branch + --auto --squash
    "PR",                   # Draft PR for human review
    "REJECT",
    "SKIP",
]


# The orchestrator creates one worktree per role on a private branch named
# ``worktree/<role>-<run_id_prefix>`` (see
# ``station_orchestrator.py`` ``git worktree add -b``). These branches are
# isolation primitives — teammates are expected to ``git checkout -b`` a
# feature branch on top before committing. When teammates skip that step
# the manager echoes the worktree branch into the verdict, and pushing it
# from the base workspace fails with a confusing "src refspec does not
# match any" because the branch lives only inside the worktree's checkout.
#
# Reject such verdicts up-front with a clear cause so operators don't
# have to decode the push stderr to understand what happened. The check
# is conservative: it only catches the orchestrator's own naming
# convention, which matches ``worktree/<role>-<8 hex chars>``.
_WORKTREE_BRANCH_RE = re.compile(r"^worktree/[a-z]+-[0-9a-fA-F]{4,}$")


def _is_worktree_isolation_branch(branch: str) -> bool:
    """Return True iff ``branch`` matches the orchestrator's per-role
    worktree-isolation naming. Used by every push-capable executor to
    refuse pushes that would 404 against origin."""
    return bool(_WORKTREE_BRANCH_RE.match(branch or ""))


_WORKTREE_BRANCH_ERROR = (
    "branch '{branch}' is the worktree's private isolation ref "
    "(orchestrator-created via ``git worktree add -b``). Teammates must "
    "``git checkout -b <feature-branch>`` before committing; pushing this "
    "ref from the base workspace would 404. Reject the verdict so the "
    "teammate re-runs on a proper feature branch."
)


def _pr_number_from_url(pr_url: str) -> int | None:
    """Extract the PR number from a ``gh pr create`` URL.

    Returns ``None`` when the URL doesn't match the canonical
    ``/pull/<n>`` shape — the harness only needs the number for richer
    comments, so missing it is non-fatal.
    """
    m = re.search(r"/pull/(\d+)\b", pr_url or "")
    return int(m.group(1)) if m else None


def _poll_pr_mergeable(
    pr_url: str,
    *,
    env: dict[str, str] | None,
    max_attempts: int = _PR_MERGEABILITY_POLL_ATTEMPTS,
    delay_s: float = _PR_MERGEABILITY_POLL_DELAY_S,
) -> str | None:
    """Return the PR's settled ``mergeable`` state, or ``None`` on error.

    GitHub computes ``mergeable`` asynchronously after a PR is opened —
    the first ``gh pr view`` call against a fresh PR almost always
    returns ``"UNKNOWN"``. Poll a small number of times with a short
    delay until the state settles or attempts run out.

    Returns one of ``"MERGEABLE"`` / ``"CONFLICTING"`` / ``"UNKNOWN"``
    (when polling timed out) or ``None`` when ``gh`` failed. The caller
    is expected to act only on ``"CONFLICTING"`` — any other value
    means "leave the PR alone."
    """
    state: str | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(delay_s)
        res = gh_run(
            ["pr", "view", pr_url,
             "--json", "mergeable", "-q", ".mergeable"],
            env=env,
        )
        if not res.ok:
            return None
        state = (res.stdout or "").strip() or None
        if state and state != "UNKNOWN":
            return state
    return state


def _resolve_pr_conflict_if_needed(
    verdict: "Verdict",
    *,
    pr_url: str,
    workspace: Path,
    run_id: str | None,
    env: dict[str, str] | None,
    into: "ExecutionResult",
) -> None:
    """If the just-opened PR is CONFLICTING, invoke ``resolve-conflicts.sh``
    to rebase + (optionally) LLM-resolve + push.

    Best-effort: any failure is logged and recorded in
    ``into.actions`` so operators can see what happened, but the
    verdict itself stays successful. The PR remains open for manual
    intervention if the resolver can't recover; auto-merge is armed
    independently in the caller, so a later operator-driven resolve
    will re-trigger evaluation.

    The harness assumes ``workspace`` HEAD is already on the feature
    branch; we run ``git checkout`` first.
    """
    mergeable = _poll_pr_mergeable(pr_url, env=env)
    if mergeable != "CONFLICTING":
        # MERGEABLE, UNKNOWN (poll timed out), or gh-error — nothing
        # actionable. Recording the state helps the digest show that
        # we checked.
        if mergeable:
            into.with_action(f"mergeable={mergeable}; resolver not invoked")
        return

    into.with_action("mergeable=CONFLICTING; invoking conflict resolver")

    # The harness ``cd``s into workspace then ``git rebase origin/<base>``
    # without checking out the feature branch — see
    # ``agent/scripts/resolve-conflicts.sh`` and our 2026-05-21 manual
    # run where rebasing-while-on-main produced a wrong-branch attempt.
    co = subprocess.run(
        ["git", "checkout", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if co.returncode != 0:
        logger.warning(
            "verdict_execution: checkout %s failed before conflict-resolve: %s",
            verdict.branch, co.stderr.strip()[:200],
        )
        into.with_action(
            f"resolver skipped: checkout {verdict.branch} failed"
        )
        return

    args = [
        "bash", str(_RESOLVE_CONFLICTS_SCRIPT),
        "--workspace", str(workspace),
        "--branch", verdict.branch,
        "--base", verdict.base_branch,
        "--repo", verdict.project,
        "--triggered-by", "at_merge",
    ]
    if run_id:
        args.extend(["--run-id", run_id])
    pr_num = _pr_number_from_url(pr_url)
    if pr_num is not None:
        args.extend(["--pr", str(pr_num)])

    proc = subprocess.run(args, capture_output=True, text=True, env=env)
    if proc.returncode == 0:
        # The harness pushed the resolved branch; the armed auto-merge
        # (caller's step 3) will re-evaluate and land the PR once
        # branch protection is satisfied.
        into.with_action("conflict resolver: resolved + pushed")
    else:
        # Exit codes from the harness: 10=tests-failed, 11=manager-rejected,
        # 99=budget-exhausted, 1=unrecoverable. None of these invalidate
        # the verdict — the PR exists and a human can take over.
        logger.warning(
            "verdict_execution: conflict resolver exited %d for %s: %s",
            proc.returncode, pr_url,
            (proc.stderr or "").strip()[:200],
        )
        into.with_action(
            f"conflict resolver exit={proc.returncode}"
        )


@dataclass
class Verdict:
    """Parsed verdict from the manager's verdicts JSON. Mirrors the
    field names the bash reads at run-manager.sh:2027 onward.
    """

    project: str
    issue_number: int | None
    verdict: VerdictKind
    branch: str
    base_branch: str = "main"
    reasoning: str = ""
    mode: str = "full"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verdict":
        return cls(
            project=str(data.get("project") or ""),
            issue_number=_optional_int(data.get("issue_number")),
            verdict=str(data.get("verdict") or "REJECT"),  # type: ignore[arg-type]
            branch=str(data.get("branch") or ""),
            base_branch=str(data.get("base_branch") or "main"),
            reasoning=str(data.get("reasoning") or ""),
            mode=str(data.get("mode") or "full"),
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ExecutionResult:
    """Outcome of executing one verdict. The dispatcher returns one of
    these per verdict so the caller can fold them into a run summary
    or surface failures in a webhook payload.
    """

    verdict: VerdictKind
    project: str
    issue_number: int | None
    success: bool
    pr_url: str | None = None
    error: str | None = None
    actions: list[str] = field(default_factory=list)

    def with_action(self, action: str) -> "ExecutionResult":
        self.actions.append(action)
        return self


def _build_pr_body(verdict: Verdict, *, run_id: str | None = None) -> str:
    body = verdict.reasoning or "(no reasoning supplied)"
    if verdict.issue_number is not None:
        # Use the close-keywords plural form so multi-issue PRs work too;
        # GitHub also accepts the singular for a one-issue PR.
        body += f"\n\nCloses #{verdict.issue_number}"
    if run_id:
        body += f"\n\n---\nAutonomous run: {run_id}"
    return body


def _resolve_pr_base(verdict: Verdict, dev_branch: str | None) -> str:
    """Return the branch to target with ``gh pr create --base``.

    When ``integration.enabled`` is true in the manager config,
    ``project_loop`` passes the configured ``integration.dev_branch`` down
    as ``dev_branch``. In that mode the integration branch is the SOLE
    legal base for autonomous PRs — the operator promotes integration to
    trunk separately. Without this override, the executor would happily
    open a PR (and arm auto-merge) against whatever ``base_branch`` the
    employee reported, which on repos without a ``CLAUDE.md`` defaults
    to ``main``. That regression was introduced when ``APPROVE`` and
    ``APPROVE_INTEGRATION`` were collapsed (commit 68dca3c / PR #475):
    the old ``APPROVE_INTEGRATION`` path targeted the integration branch
    explicitly, and dropping that distinction silently routed every
    APPROVE to trunk for projects with no per-repo workflow doc.

    ``dev_branch=None`` means integration is disabled (or the caller is
    test code without the config wired in) — fall back to the verdict's
    reported base so existing behaviour is preserved.
    """
    return dev_branch or verdict.base_branch


def _ensure_remote_branch(
    workspace: Path,
    repo: str,
    branch: str,
    fallback_base: str,
    env: dict[str, str] | None,
) -> tuple[bool, str | None]:
    """Make sure ``branch`` exists on ``origin``; create it from
    ``fallback_base`` if missing.

    Returns ``(ok, error_message)``. The remote-branch lookup uses
    ``git ls-remote`` so we don't need extra GitHub-API permissions
    beyond what the existing push uses. Bootstrap pushes
    ``refs/remotes/origin/<fallback_base>:refs/heads/<branch>`` so the
    new branch is anchored at the exact same commit as the trunk tip we
    know about — no working-tree state involved.

    ``repo`` is unused today (the workspace already has its origin set
    up by ``ensure_workspace``); kept on the signature for future moves
    to a pure-API bootstrap that doesn't require a local clone.
    """
    del repo  # Reserved — see docstring.

    ls = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if ls.returncode != 0:
        return False, f"git ls-remote failed: {ls.stderr.strip()[:200]}"
    if ls.stdout.strip():
        return True, None  # branch already exists upstream

    # Make sure we have the base on origin/<fallback_base> before we try
    # to push it under a new ref name. Without a prior fetch the local
    # ``refs/remotes/origin/<fallback_base>`` may not exist (e.g. a fresh
    # shallow workspace), and the push would 404 on a missing src.
    fetch = subprocess.run(
        ["git", "fetch", "origin", fallback_base],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if fetch.returncode != 0:
        return False, f"git fetch base failed: {fetch.stderr.strip()[:200]}"

    push = subprocess.run(
        [
            "git", "push", "origin",
            f"refs/remotes/origin/{fallback_base}:refs/heads/{branch}",
        ],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        return False, (
            f"bootstrap of integration branch '{branch}' from "
            f"'{fallback_base}' failed: {push.stderr.strip()[:200]}"
        )
    return True, None


def execute_approve(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,
) -> ExecutionResult:
    """Push the branch, open a non-draft PR, arm auto-merge, close the issue.

    APPROVE = "ready to land." The executor opens a PR and arms
    ``gh pr merge --auto --squash``. Branch protection on the base ref
    decides whether the merge happens immediately (no required checks)
    or waits on CI/required reviews. Either way the operator's branch
    protection rules — not the verdict logic — gate the merge.

    Before 2026-05-21 there was a separate ``APPROVE_INTEGRATION`` verdict
    that did the auto-merge while ``APPROVE`` only opened the PR. The
    manager prompt's decision tree, verdict description, and confidence
    table all conspired to pick ``APPROVE`` for routine completed work,
    which meant PRs piled up open with no one to merge them. The two
    verdicts have been collapsed: APPROVE_INTEGRATION is now an alias
    that delegates to this function (preserved for backward compat with
    stored verdicts and the executor dispatch table).

    When ``dev_branch`` is set, the PR is opened against it instead of
    the verdict's reported ``base_branch`` — see :func:`_resolve_pr_base`.
    ``project_loop`` passes ``dev_branch`` whenever ``integration.enabled``
    is true in the manager config. ``None`` preserves the legacy
    behaviour of trusting the employee-reported base.
    """
    result = ExecutionResult(
        verdict="APPROVE",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    # 0. Safety net: refuse to push worktree-isolation branches.
    if _is_worktree_isolation_branch(verdict.branch):
        result.error = _WORKTREE_BRANCH_ERROR.format(branch=verdict.branch)
        return result

    # 1. git push origin <branch>
    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    # 1.5. When integration mode is enabled the PR must land on the
    # configured integration branch (e.g. ``claude-agent-station``), not
    # whatever the employee reported as ``base_branch`` (typically
    # ``main``). Ensure the branch exists on origin before pointing the
    # PR at it; bootstrap from the employee-reported base so the new
    # integration branch starts at a known-good commit.
    pr_base = _resolve_pr_base(verdict, dev_branch)
    if dev_branch is not None and dev_branch != verdict.base_branch:
        ok, err = _ensure_remote_branch(
            workspace=workspace,
            repo=verdict.project,
            branch=dev_branch,
            fallback_base=verdict.base_branch,
            env=env,
        )
        if not ok:
            result.error = err
            return result
        result.with_action(f"ensure integration branch {dev_branch!r} on origin")

    # 2. gh pr create — non-draft, base from the resolved integration
    # branch (when enabled) or the verdict's base_branch (when not).
    pr = gh_run(
        [
            "pr", "create",
            "--repo", verdict.project,
            "--head", verdict.branch,
            "--base", pr_base,
            "--title", _pr_title(verdict),
            "--body", _build_pr_body(verdict, run_id=run_id),
        ],
        env=env,
    )
    if not pr.ok:
        result.error = f"gh pr create failed: {pr.stderr.strip()[:200]}"
        return result
    result.pr_url = pr.stdout.strip()
    result.with_action(f"gh pr create → {result.pr_url}")

    # 2.5. If the PR is conflicting against the base, invoke the
    # conflict resolver to rebase + (optionally) LLM-resolve + push.
    # Done BEFORE arming auto-merge so the auto-merge picks up the
    # resolved branch on its first re-evaluation rather than stalling
    # on the original conflict. Best-effort — failures are recorded in
    # ``result.actions`` but do not invalidate the verdict; a human
    # can take over via the open PR. See
    # :func:`_resolve_pr_conflict_if_needed`.
    _resolve_pr_conflict_if_needed(
        verdict,
        pr_url=result.pr_url,
        workspace=workspace,
        run_id=run_id,
        env=env,
        into=result,
    )

    # 3. gh pr merge --auto --squash. Best-effort: a failure to arm
    # auto-merge (branch protection misconfigured, repo doesn't allow
    # squash, etc.) does not invalidate the PR. The operator's branch
    # protection rules are the merge gate; --auto just means "merge
    # when those rules allow."
    merge = gh_run(
        ["pr", "merge", "--auto", "--squash", result.pr_url],
        env=env,
    )
    if merge.ok:
        result.with_action("gh pr merge --auto --squash")
    else:
        logger.warning(
            "verdict_execution: auto-merge arm failed for %s: %s",
            result.pr_url, merge.stderr.strip()[:200],
        )
        result.with_action(
            f"gh pr merge --auto failed: {merge.stderr.strip()[:80]}"
        )

    # 4. issue comment (best-effort; do not fail the verdict on this)
    if verdict.issue_number is not None:
        _post_issue_comment(verdict, body_prefix="## Manager verdict: APPROVED",
                            run_id=run_id, env=env, into=result)

    # 4. issue close (best-effort; #460). Gated on pr_url so we only
    # close when there's a PR to point at — guards against degraded
    # paths where gh pr create succeeded silently with no output.
    if result.pr_url:
        _close_issues(verdict, pr_url=result.pr_url, run_id=run_id,
                      env=env, into=result)

    result.success = True
    return result


def execute_pr(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    draft: bool = True,
    dev_branch: str | None = None,
) -> ExecutionResult:
    """Open a draft PR (or marked-ready) for manual review. Same path as
    APPROVE but with ``--draft`` and a different issue comment.

    ``dev_branch`` is honored identically to :func:`execute_approve`:
    when set, the PR opens against the integration branch rather than
    the employee-reported base. See :func:`_resolve_pr_base`.
    """
    result = ExecutionResult(
        verdict="PR",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    if _is_worktree_isolation_branch(verdict.branch):
        result.error = _WORKTREE_BRANCH_ERROR.format(branch=verdict.branch)
        return result

    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    pr_base = _resolve_pr_base(verdict, dev_branch)
    if dev_branch is not None and dev_branch != verdict.base_branch:
        ok, err = _ensure_remote_branch(
            workspace=workspace,
            repo=verdict.project,
            branch=dev_branch,
            fallback_base=verdict.base_branch,
            env=env,
        )
        if not ok:
            result.error = err
            return result
        result.with_action(f"ensure integration branch {dev_branch!r} on origin")

    pr_args = [
        "pr", "create",
        "--repo", verdict.project,
        "--head", verdict.branch,
        "--base", pr_base,
        "--title", _pr_title(verdict),
        "--body", _build_pr_body(verdict, run_id=run_id),
    ]
    if draft:
        pr_args.append("--draft")
    pr = gh_run(pr_args, env=env)
    if not pr.ok:
        result.error = f"gh pr create failed: {pr.stderr.strip()[:200]}"
        return result
    result.pr_url = pr.stdout.strip()
    result.with_action(f"gh pr create{' --draft' if draft else ''} → {result.pr_url}")

    if verdict.issue_number is not None:
        _post_issue_comment(
            verdict,
            body_prefix="## Manager verdict: PR opened for human review",
            run_id=run_id, env=env, into=result,
        )
    result.success = True
    return result


def execute_reject(
    verdict: Verdict,
    *,
    workspace: Path | None = None,  # noqa: ARG001 — unused, kept for caller symmetry
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,  # noqa: ARG001 — dispatcher symmetry; see execute_approve
) -> ExecutionResult:
    """Comment on the issue and apply the reject label. No branch
    operations — the feature branch stays local for the operator to
    inspect / rerun.
    """
    result = ExecutionResult(
        verdict="REJECT",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=True,  # rejecting is "successful execution of the reject"
    )
    if verdict.issue_number is None:
        result.with_action("skip (no issue_number)")
        return result

    _post_issue_comment(
        verdict, body_prefix="🤖 **Manager verdict: REJECTED**",
        run_id=run_id, env=env, into=result,
    )
    # Clear the in-progress / done labels so the issue surfaces in the
    # next pick_issue pass as eligible work. Mirrors the bash REJECT
    # path at run-manager.sh:2196-2197. No new label is added — the
    # comment alone signals the rejection to humans.
    for label in ("autonomous-agent/in-progress", "autonomous-agent/done"):
        gh_run(
            [
                "issue", "edit", str(verdict.issue_number),
                "--repo", verdict.project,
                "--remove-label", label,
            ],
            env=env,
        )
        result.with_action(f"remove-label {label}")
    return result


def execute_skip(
    verdict: Verdict,
    *,
    workspace: Path | None = None,  # noqa: ARG001 — kept for symmetry
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,  # noqa: ARG001 — dispatcher symmetry; see execute_approve
) -> ExecutionResult:
    """SKIP is observably similar to REJECT but with a kinder message
    (the manager chose not to act this cycle, not that the work was bad).
    """
    result = ExecutionResult(
        verdict="SKIP",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=True,
    )
    if verdict.issue_number is None:
        result.with_action("skip (no issue_number)")
        return result

    _post_issue_comment(
        verdict, body_prefix="🤖 **Manager verdict: SKIPPED this cycle**",
        run_id=run_id, env=env, into=result,
    )
    return result


def execute_approve_integration(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,  # noqa: ARG001 — retained for caller-API stability
) -> ExecutionResult:
    """**Deprecated.** Alias for :func:`execute_approve`.

    APPROVE and APPROVE_INTEGRATION used to be distinct verdicts: the
    former opened a PR without arming auto-merge, the latter armed it.
    The manager prompt's decision tree + verdict description + confidence
    table all conspired to pick APPROVE for routine work, leaving PRs
    open with no one to merge them (run-20260521T210606Z produced #6
    and #7 in laboef1900/LCM that sat untouched). The two verdicts have
    been collapsed: APPROVE now always arms auto-merge, branch
    protection on the base ref gates the actual merge.

    This shim preserves the old verdict name for backward compatibility
    with stored verdicts, the executor dispatch table, and any tooling
    that still emits APPROVE_INTEGRATION. The returned
    ``ExecutionResult.verdict`` is patched back to ``"APPROVE_INTEGRATION"``
    so telemetry consumers can still distinguish them.
    """
    result = execute_approve(
        verdict,
        workspace=workspace,
        run_id=run_id,
        env=env,
        dev_branch=dev_branch,
    )
    result.verdict = "APPROVE_INTEGRATION"
    return result


_EXECUTORS = {
    "APPROVE": execute_approve,
    "APPROVE_INTEGRATION": execute_approve_integration,
    "PR": execute_pr,
    "REJECT": execute_reject,
    "SKIP": execute_skip,
}


def execute(verdict: Verdict, **kwargs) -> ExecutionResult:
    """Dispatch to the right execute_* helper based on ``verdict.verdict``.

    Unknown verdicts are coerced to REJECT (the bash's safe default) so
    a malformed manager output doesn't push code on accident.
    """
    fn = _EXECUTORS.get(verdict.verdict, execute_reject)
    return fn(verdict, **kwargs)


# ── Helpers ───────────────────────────────────────────────────────────

# #460: extract issue numbers from branch names. Supports both
# 'feature/{role}-issues-29-30-...' (multi-issue, group 1 captures '29-30')
# and 'autonomous/issue-31' (single-issue, group 2 captures '31').
_BRANCH_ISSUES_RE = re.compile(r"\bissues?-(\d+(?:-\d+)*)\b|/issue-(\d+)\b")


def _issue_numbers_from_branch(branch: str) -> list[int]:
    """Parse issue numbers from a branch name. Returns sorted deduplicated list.

    Handles both branch-naming conventions:
    - 'feature/{role}-issues-29-30-...' (multi-issue)
    - 'autonomous/issue-31' (single-issue)

    Used by _resolve_issue_numbers and by agent.workspace_setup's prune helper.
    """
    numbers: set[int] = set()
    for match in _BRANCH_ISSUES_RE.finditer(branch or ""):
        multi = match.group(1)
        single = match.group(2)
        if multi:
            for chunk in multi.split("-"):
                if chunk.isdigit():
                    numbers.add(int(chunk))
        elif single and single.isdigit():
            numbers.add(int(single))
    return sorted(numbers)


def _resolve_issue_numbers(verdict: Verdict) -> list[int]:
    """Return the union of branch-name-extracted issue numbers and
    verdict.issue_number, deduplicated and sorted ascending.

    Real-world data shows ``verdict.issue_number`` is unreliable —
    multi-issue branches like ``feature/backend-issues-29-30-...`` are
    routinely emitted with only one of the numbers in the field, or
    None. The branch name is a more reliable source. #460.
    """
    numbers: set[int] = set()
    if verdict.issue_number is not None:
        numbers.add(verdict.issue_number)
    numbers.update(_issue_numbers_from_branch(verdict.branch or ""))
    return sorted(numbers)


def _close_issues(
    verdict: Verdict,
    *,
    pr_url: str,
    run_id: str | None,
    env: dict[str, str] | None,
    into: ExecutionResult,
) -> None:
    """Close every issue addressed by this verdict via ``gh issue close``.

    Best-effort. Each failure is logged at WARNING and the helper
    continues to the next issue. The verdict's success state is
    never affected.

    Closes the union of branch-name-extracted issue numbers and
    ``verdict.issue_number`` (see :func:`_resolve_issue_numbers`).
    Idempotent via ``gh`` — already-closed issues return an error
    that we swallow.

    #460.
    """
    issue_numbers = _resolve_issue_numbers(verdict)
    if not issue_numbers:
        return

    body_parts = ["Closed by autonomous agent"]
    if run_id:
        body_parts.append(run_id)
    body_parts.append(f"via PR {pr_url}")
    body = " ".join(body_parts) + "."

    for issue_number in issue_numbers:
        result = gh_run(
            [
                "issue", "close", str(issue_number),
                "--repo", verdict.project,
                "--reason", "completed",
                "--comment", body,
            ],
            env=env,
        )
        if result.ok:
            into.with_action(f"gh issue close #{issue_number}")
        else:
            logger.warning(
                "verdict_execution: gh issue close failed for %s#%s: %s",
                verdict.project, issue_number, result.stderr.strip()[:200],
            )


def _pr_title(verdict: Verdict) -> str:
    if verdict.issue_number is not None:
        return f"autonomous: resolve #{verdict.issue_number}"
    return f"autonomous: {verdict.branch}"


def _post_issue_comment(
    verdict: Verdict,
    *,
    body_prefix: str,
    run_id: str | None,
    env: dict[str, str] | None,
    into: ExecutionResult,
) -> None:
    body = body_prefix
    if verdict.reasoning:
        body += f" — {verdict.reasoning}"
    if run_id:
        body += f"\n\nRun: {run_id}"
    if verdict.issue_number is None:
        return
    result = gh_run(
        [
            "issue", "comment", str(verdict.issue_number),
            "--repo", verdict.project,
            "--body", body,
        ],
        env=env,
    )
    if result.ok:
        into.with_action(f"gh issue comment #{verdict.issue_number}")
    else:
        logger.warning(
            "verdict_execution: gh issue comment failed for %s#%s: %s",
            verdict.project, verdict.issue_number, result.stderr.strip()[:200],
        )
