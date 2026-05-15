"""Scheduler dependency / failure semantics (#391).

Eligibility rules under test:

1. Independent sub-issues (no ``depends_on_number``) are eligible.
2. A sub-issue with ``depends_on_number`` is eligible only after its
   prerequisite is merged into the integration branch.
3. A sibling that has failed (state != "open") does not block others —
   the splitter pipeline accepts partial completion.
4. Sub-issues still carrying the ``splitter-proposed`` label are
   considered unapproved by the operator and held back.
"""
from __future__ import annotations

from agent.issue_splitter.scheduler import pick_eligible_subruns


def _sub(number: int, *, depends_on_number: int | None = None,
         state: str = "open", merged: bool = False,
         labels: list[str] | None = None) -> dict:
    """Helper to build a sub-issue dict for the scheduler tests.

    Default labels is ``[]`` (approved). Pass ``labels=["splitter-proposed"]``
    to opt into the unapproved/pending-review case.
    """
    return {
        "number": number,
        "labels": labels if labels is not None else [],
        "depends_on_number": depends_on_number,
        "state": state,
        "merged_into_integration": merged,
    }


def test_picks_all_independents_first():
    subs = [_sub(101), _sub(102), _sub(103, depends_on_number=101)]
    eligible = pick_eligible_subruns(subs)
    numbers = {s["number"] for s in eligible}
    assert numbers == {101, 102}


def test_dependent_unlocks_after_prereq_merged():
    subs = [_sub(101, merged=True), _sub(102, depends_on_number=101)]
    eligible = pick_eligible_subruns(subs)
    assert {s["number"] for s in eligible} == {102}


def test_failed_sibling_does_not_block_others():
    subs = [_sub(101, state="closed"), _sub(102)]
    eligible = pick_eligible_subruns(subs)
    assert {s["number"] for s in eligible} == {102}


def test_unapproved_sub_not_picked():
    subs = [_sub(101, labels=["splitter-proposed"])]
    eligible = pick_eligible_subruns(subs)
    assert eligible == []
