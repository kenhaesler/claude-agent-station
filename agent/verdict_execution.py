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
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.gh_client import gh_run

logger = logging.getLogger(__name__)

VerdictKind = Literal["APPROVE", "PR", "REJECT", "SKIP"]


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
) -> ExecutionResult:
    """Push the branch, open a PR, comment on the issue.

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
    result.success = True
    return result


def execute_pr(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    draft: bool = True,
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


_EXECUTORS = {
    "APPROVE": execute_approve,
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
