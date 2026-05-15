"""maybe_split heuristic tests (#391)."""
from __future__ import annotations

from agent.issue_splitter.heuristics import (
    HeuristicResult,
    LONG_BODY_TOKENS,
    maybe_split,
)


def _issue(*, body: str = "", labels: tuple[str, ...] = ()) -> dict:
    return {"number": 27, "title": "auth", "body": body, "labels": list(labels)}


def test_short_simple_issue_does_not_split():
    res = maybe_split(_issue(body="Add a tooltip."))
    assert isinstance(res, HeuristicResult)
    assert res.should_split is False
    assert res.reasons == ()


def test_long_body_triggers_split():
    body = "x " * (LONG_BODY_TOKENS + 100)
    res = maybe_split(_issue(body=body))
    assert res.should_split is True
    assert "body_length" in res.reasons


def test_four_acceptance_criteria_triggers_split():
    body = (
        "## Acceptance criteria\n"
        "- [ ] login api\n"
        "- [ ] me endpoint\n"
        "- [ ] oauth callback\n"
        "- [ ] route middleware\n"
    )
    res = maybe_split(_issue(body=body))
    assert res.should_split is True
    assert "acceptance_count" in res.reasons


def test_cross_cutting_labels_trigger_split():
    res = maybe_split(_issue(labels=("backend", "frontend", "db-migration")))
    assert res.should_split is True
    assert "cross_cutting" in res.reasons


def test_split_me_label_forces_split():
    res = maybe_split(_issue(body="x", labels=("split-me",)))
    assert res.should_split is True
    assert "opt_in" in res.reasons


def test_do_not_split_label_forces_no_split():
    body = "x " * (LONG_BODY_TOKENS + 500)
    res = maybe_split(_issue(body=body, labels=("do-not-split",)))
    assert res.should_split is False
    assert "opt_out" in res.reasons


def test_opt_out_beats_opt_in():
    """Operator escape hatch wins over operator opt-in."""
    res = maybe_split(_issue(labels=("split-me", "do-not-split")))
    assert res.should_split is False
    assert "opt_out" in res.reasons


def test_missing_body_field_is_safe():
    """Real GitHub issues sometimes omit the body field entirely."""
    res = maybe_split({"number": 1, "title": "x", "labels": []})
    assert res.should_split is False


def test_multiple_triggers_are_all_reported():
    body = ("x " * (LONG_BODY_TOKENS + 50)) + (
        "\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n"
    )
    res = maybe_split(_issue(body=body, labels=("backend", "frontend", "infra")))
    assert res.should_split is True
    assert {"body_length", "acceptance_count", "cross_cutting"} <= set(res.reasons)
