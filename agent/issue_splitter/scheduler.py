"""Sub-run scheduler (#391).

Picks 0-N eligible sub-issues per scheduler tick. A sub-issue is eligible
when:

1. Its issue is still open.
2. Its ``splitter-proposed`` label is **absent** — present means the
   operator hasn't approved it for autonomous pickup yet. (Same gate
   as ``vision-suggested``.)
3. Its ``depends_on_number`` is either absent or has been merged into
   the integration branch.

The caller (the existing run-trigger flow) is responsible for spawning
the run; this module only decides *which* sub-issues are ready. Keeping
the dispatch decision out of this pure function lets the scheduler be
exercised with synthetic dicts in unit tests without touching GitHub
or the run-launcher.
"""
from __future__ import annotations

from typing import Iterable

SPLITTER_LABEL = "splitter-proposed"


def _is_approved(sub: dict) -> bool:
    """True when the operator has removed the ``splitter-proposed`` label.

    Absence of the label is the approval signal — same convention used
    by the vision-bootstrap flow (``vision-suggested``). This keeps the
    operator-review UX consistent across the two proposal-style flows.
    """
    return SPLITTER_LABEL not in (sub.get("labels") or ())


def _prereq_satisfied(sub: dict, by_number: dict[int, dict]) -> bool:
    """True when this sub-issue has no prereq or its prereq is merged.

    A missing prereq (caller referenced ``depends_on_number=N`` but N is
    not in the sibling set) returns False rather than True — that's a
    bug in the splitter output and we prefer to stall the dependant
    rather than silently dispatch it out of order.
    """
    dep = sub.get("depends_on_number")
    if dep is None:
        return True
    prereq = by_number.get(dep)
    if prereq is None:
        return False
    return bool(prereq.get("merged_into_integration"))


def pick_eligible_subruns(subs: Iterable[dict]) -> list[dict]:
    """Return the subset of *subs* that may be dispatched this tick.

    Pure function over the sibling state — caller is responsible for
    fetching the dict shape (typically a GitHub issue payload with
    project-side ``merged_into_integration`` annotated on top).
    """
    subs = list(subs)
    by_number = {s["number"]: s for s in subs}
    eligible: list[dict] = []
    for sub in subs:
        if sub.get("state") != "open":
            continue
        # Already-merged subs are done — even if the GitHub issue is still
        # technically "open", we don't re-dispatch a run for it. The
        # ``merged_into_integration`` flag is the canonical "this slice
        # has shipped" signal maintained by the integration-branch flow.
        if sub.get("merged_into_integration"):
            continue
        if not _is_approved(sub):
            continue
        if not _prereq_satisfied(sub, by_number):
            continue
        eligible.append(sub)
    return eligible
