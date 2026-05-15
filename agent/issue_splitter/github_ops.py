"""GitHub issue creation for the issue-splitter (#391).

Mirrors the issue-creation flow used by ``agent/vision_analyst.py`` (which
seeds a ``vision-suggested`` label, then attaches it to every proposal it
creates) but with a different label (``splitter-proposed``) and a per-
sub-issue body template that inlines the ``Parent: #N`` back-link plus
optional ``Depends on #M`` cross-reference.

Why the ``gh`` parameter is an injected client rather than direct
``subprocess.run(["gh", ...])`` calls (the pattern in ``vision_analyst``):

- Three sites (label ensure, issue create, parent comment) need to
  share state — the controller in PR-3 wants a single retry/auth/error
  policy across all of them. A method-style seam keeps that policy in
  one place instead of duplicating it inline in this module.
- The seam is trivially mockable, so unit tests don't have to patch
  ``subprocess.run`` and reason about argv shape; they assert on the
  intent (``gh.create_issue(... labels=[...])``) instead.

PR-3 provides the concrete ``gh`` adapter that wraps ``agent.gh_client``.
"""
from __future__ import annotations

from typing import Iterable, Protocol, Sequence

from agent.issue_splitter.schema import SubIssueProposal

SPLITTER_LABEL = "splitter-proposed"
SPLITTER_LABEL_COLOR = "0E8A16"  # matches existing PR:Approved green
SPLITTER_LABEL_DESCRIPTION = (
    "Issue proposed by Claude Station's issue-splitter agent. Review and "
    "remove this label to make the issue eligible for autonomous pickup."
)


class _GhClient(Protocol):
    """Method shape this module relies on. PR-3 supplies the adapter."""

    def label_exists(self, owner: str, repo: str, name: str) -> bool: ...
    def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        *,
        color: str,
        description: str,
    ) -> None: ...
    def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: Sequence[str],
    ) -> dict: ...
    def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        body: str,
    ) -> None: ...


def ensure_splitter_label(owner: str, repo: str, gh: _GhClient) -> None:
    """Create the ``splitter-proposed`` label on ``owner/repo`` if missing.

    Idempotent via ``gh.label_exists`` so callers can invoke this on every
    splitter run without paying a create-and-ignore-409 round trip.
    """
    if gh.label_exists(owner, repo, SPLITTER_LABEL):
        return
    gh.create_label(
        owner,
        repo,
        SPLITTER_LABEL,
        color=SPLITTER_LABEL_COLOR,
        description=SPLITTER_LABEL_DESCRIPTION,
    )


def _format_body(
    parent_number: int,
    proposal: SubIssueProposal,
    sibling_numbers_by_index: dict[int, int],
) -> str:
    lines = [
        f"_This sub-issue was proposed by Claude Station's issue-splitter "
        f"from parent #{parent_number}. Review by removing the "
        f"`{SPLITTER_LABEL}` label._",
        "",
        f"Parent: #{parent_number}",
    ]
    if proposal.depends_on is not None:
        # depends_on is a zero-based index into the proposals tuple; the
        # caller populates sibling_numbers_by_index as it creates each
        # issue, so any back-reference is resolvable by the time we see
        # it (the schema guarantees depends_on < own_index path the
        # splitter produces, and parse rejects self-references).
        prereq_number = sibling_numbers_by_index[proposal.depends_on]
        lines.append(f"Depends on #{prereq_number}")
    lines += ["", proposal.body, "", "## Acceptance criteria", ""]
    lines += [f"- [ ] {c}" for c in proposal.acceptance]
    return "\n".join(lines)


def create_sub_issues(
    parent: dict,
    proposals: Sequence[SubIssueProposal],
    gh: _GhClient,
) -> list[dict]:
    """Create one GitHub issue per proposal, returning the raw API payloads.

    Each sub-issue gets ``SPLITTER_LABEL`` plus the union of the parent's
    labels and the proposal's labels. The union (sorted for determinism)
    means a parent's ``backend`` label carries forward without forcing
    the splitter prompt to re-emit it, and proposal-specific labels like
    ``frontend`` for a UI-only slice still attach.
    """
    owner, repo = parent["repo"].split("/", 1)
    ensure_splitter_label(owner, repo, gh)

    parent_labels = set(parent.get("labels") or ())
    created: list[dict] = []
    sibling_numbers: dict[int, int] = {}
    for i, prop in enumerate(proposals):
        body = _format_body(parent["number"], prop, sibling_numbers)
        labels = sorted({SPLITTER_LABEL, *parent_labels, *prop.labels})
        issue = gh.create_issue(
            owner,
            repo,
            title=prop.title,
            body=body,
            labels=labels,
        )
        sibling_numbers[i] = issue["number"]
        created.append(issue)
    return created


def add_backlink_comment(
    *,
    parent_repo: str,
    parent_number: int,
    sub_numbers: Iterable[int],
    gh: _GhClient,
) -> None:
    """Post a comment on the parent listing the new sub-issues.

    The comment doubles as a discoverable audit trail — anyone landing on
    the parent issue sees the decomposition without having to find the
    splitter's run record in the dashboard.
    """
    owner, repo = parent_repo.split("/", 1)
    lines = [
        "Claude Station's issue-splitter has decomposed this issue:",
        "",
    ]
    lines += [f"- #{n}" for n in sub_numbers]
    lines += [
        "",
        "Each sub-issue requires manual approval (remove the "
        f"`{SPLITTER_LABEL}` label) before autonomous pickup.",
    ]
    gh.create_issue_comment(owner, repo, parent_number, body="\n".join(lines))
