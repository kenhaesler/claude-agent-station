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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.gh_client import gh_run

logger = logging.getLogger(__name__)

VerdictKind = Literal[
    "APPROVE",              # Direct merge to base (today's APPROVE)
    "APPROVE_INTEGRATION",  # Non-draft PR against integration branch + --auto --squash
    "PR",                   # Draft PR for human review
    "REJECT",
    "SKIP",
]


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


def execute_approve(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,  # noqa: ARG001 — kept for dispatcher symmetry
) -> ExecutionResult:
    """Push the branch, open a PR, comment on the issue.

    ``dev_branch`` is accepted but ignored — the dispatcher passes the
    same kwargs to every executor, and only ``execute_approve_integration``
    consumes it. Without the parameter here, the dispatcher's blind
    ``**kwargs`` passthrough raises TypeError and every APPROVE silently
    failed before this fix.

    Does NOT auto-merge, does NOT close the issue — those decisions live
    in the bash path that consumes this module (deferred follow-up).
    """
    result = ExecutionResult(
        verdict="APPROVE",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    # 1. git push origin <branch>
    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    # 2. gh pr create
    pr = gh_run(
        [
            "pr", "create",
            "--repo", verdict.project,
            "--head", verdict.branch,
            "--base", verdict.base_branch,
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

    # 3. issue comment (best-effort; do not fail the verdict on this)
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
    dev_branch: str | None = None,  # noqa: ARG001 — dispatcher symmetry; see execute_approve
) -> ExecutionResult:
    """Open a draft PR (or marked-ready) for manual review. Same path as
    APPROVE but with ``--draft`` and a different issue comment.
    """
    result = ExecutionResult(
        verdict="PR",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    pr_args = [
        "pr", "create",
        "--repo", verdict.project,
        "--head", verdict.branch,
        "--base", verdict.base_branch,
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
    dev_branch: str | None = None,
) -> ExecutionResult:
    """Push the branch, open a non-draft PR against the integration/dev
    branch, then arm GitHub auto-merge (``gh pr merge --auto --squash``).

    If ``dev_branch`` is None or empty, the executor degrades to
    :func:`execute_approve` with a warning — the manager should not have
    emitted this verdict against a project without integration enabled,
    but we accept rather than fail the run.
    """
    if not dev_branch:
        logger.warning(
            "APPROVE_INTEGRATION emitted without dev_branch for %s — "
            "degrading to APPROVE",
            verdict.project,
        )
        # Intentionally re-passes only the kwargs execute_approve accepts.
        # If the dispatcher gains new kwargs in the future, they are not
        # forwarded here — degradation drops them on purpose so we don't
        # surface APPROVE_INTEGRATION-specific options through APPROVE.
        return execute_approve(
            verdict, workspace=workspace, run_id=run_id, env=env,
        )

    result = ExecutionResult(
        verdict="APPROVE_INTEGRATION",
        project=verdict.project,
        issue_number=verdict.issue_number,
        success=False,
    )

    # 1. git push -u origin <branch>
    push = subprocess.run(
        ["git", "push", "-u", "origin", verdict.branch],
        cwd=str(workspace), capture_output=True, text=True, env=env,
    )
    if push.returncode != 0:
        result.error = f"git push failed: {push.stderr.strip()[:200]}"
        return result
    result.with_action("git push")

    # 2. gh pr create — NO --draft; base = integration/dev branch.
    pr = gh_run(
        [
            "pr", "create",
            "--repo", verdict.project,
            "--head", verdict.branch,
            "--base", dev_branch,
            "--title", _pr_title(verdict),
            "--body", _build_pr_body(verdict, run_id=run_id),
        ],
        env=env,
    )
    if not pr.ok:
        result.error = f"gh pr create failed: {pr.stderr.strip()[:200]}"
        return result
    result.pr_url = pr.stdout.strip()
    result.with_action(f"gh pr create (non-draft) → {result.pr_url}")

    # 3. gh pr merge --auto --squash <pr_url>. Best-effort: a failure to
    # arm auto-merge (e.g. branch protection misconfigured) does not
    # invalidate the PR itself.
    merge = gh_run(
        [
            "pr", "merge", "--auto", "--squash", result.pr_url,
        ],
        env=env,
    )
    if merge.ok:
        result.with_action("gh pr merge --auto --squash")
    else:
        logger.warning(
            "verdict_execution: auto-merge arm failed for %s: %s",
            result.pr_url, merge.stderr.strip()[:200],
        )
        result.with_action(f"gh pr merge --auto failed: {merge.stderr.strip()[:80]}")

    # 4. Issue comment (best-effort).
    if verdict.issue_number is not None:
        _post_issue_comment(
            verdict,
            body_prefix=(
                f"## Manager verdict: APPROVE_INTEGRATION — "
                f"auto-merge armed against `{dev_branch}`. CI gates merge."
            ),
            run_id=run_id, env=env, into=result,
        )

    # 5. Issue close (best-effort; #460).
    if result.pr_url:
        _close_issues(verdict, pr_url=result.pr_url, run_id=run_id,
                      env=env, into=result)

    result.success = True
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
