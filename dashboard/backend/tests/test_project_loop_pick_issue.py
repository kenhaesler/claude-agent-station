"""Tests for agent.project_loop.pick_issue (#362).

Parity goal: pick the same issue the bash's ``get_analyzable_issues``
helper would have picked, given the same gh output. We mock
``agent.gh_client.gh_json`` so the tests don't shell to a real gh.
"""

from __future__ import annotations

from unittest.mock import patch


def _issue(number: int, title: str = "t", labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "title": title,
        "labels": [{"name": n} for n in (labels or [])],
    }


def test_picks_lowest_numbered_eligible_issue():
    from agent.project_loop import pick_issue

    with patch(
        "agent.gh_client.gh_json",
        return_value=[_issue(5), _issue(3), _issue(9)],
    ):
        issue = pick_issue("owner/repo")
    assert issue is not None
    assert issue.number == 3


def test_excludes_backlog_labeled_issues():
    """`backlog` is one of the SKIP_LABELS. Mirrors the project CLAUDE.md
    rule that the agent must NEVER work on backlog issues.
    """
    from agent.project_loop import pick_issue

    with patch(
        "agent.gh_client.gh_json",
        return_value=[
            _issue(3, labels=["backlog"]),
            _issue(7, labels=[]),
        ],
    ):
        issue = pick_issue("owner/repo")
    assert issue is not None
    assert issue.number == 7


def test_excludes_all_skip_labels_in_lockstep_with_bash():
    """All six labels in the bash SKIP set must be honoured. If any is
    missed, the Python and bash pickers diverge silently.
    """
    from agent.project_loop import pick_issue, SKIP_LABELS

    issues = [
        _issue(n + 1, labels=[label])
        for n, label in enumerate(sorted(SKIP_LABELS))
    ] + [_issue(100, labels=[])]

    with patch("agent.gh_client.gh_json", return_value=issues):
        issue = pick_issue("owner/repo")
    assert issue is not None and issue.number == 100


def test_returns_none_when_all_filtered_out():
    from agent.project_loop import pick_issue

    with patch(
        "agent.gh_client.gh_json",
        return_value=[
            _issue(1, labels=["backlog"]),
            _issue(2, labels=["wontfix"]),
        ],
    ):
        assert pick_issue("owner/repo") is None


def test_returns_none_when_repo_has_no_issues():
    from agent.project_loop import pick_issue

    with patch("agent.gh_client.gh_json", return_value=[]):
        assert pick_issue("empty/repo") is None


def test_caller_can_extend_skip_set_per_call():
    """extra_skip is for project-specific labels (e.g., a project
    configures its own skip rules). Must compose with SKIP_LABELS, not
    replace it.
    """
    from agent.project_loop import pick_issue

    with patch(
        "agent.gh_client.gh_json",
        return_value=[
            _issue(1, labels=["needs-design"]),  # filtered by extra
            _issue(2, labels=["backlog"]),  # filtered by SKIP_LABELS
            _issue(3, labels=[]),  # picked
        ],
    ):
        issue = pick_issue("owner/repo", extra_skip=frozenset({"needs-design"}))
    assert issue is not None and issue.number == 3


def test_handles_issues_with_no_label_array():
    """Defensive: gh occasionally returns ``labels: null`` for issues
    on repos that have never had labels. Treat as zero labels.
    """
    from agent.project_loop import pick_issue

    with patch(
        "agent.gh_client.gh_json",
        return_value=[{"number": 4, "title": "no labels", "labels": None}],
    ):
        issue = pick_issue("owner/repo")
    assert issue is not None and issue.number == 4


def test_calls_gh_with_expected_argv():
    """Pin the exact ``gh issue list`` argv so reviewers can diff against
    the bash form. Drift here causes silent picker divergence.
    """
    from agent.project_loop import pick_issue

    with patch("agent.gh_client.gh_json", return_value=[]) as mock_gh:
        pick_issue("owner/repo")

    call = mock_gh.call_args_list[0]
    args = call.args[0]
    assert args[:6] == [
        "issue", "list",
        "--repo", "owner/repo",
        "--state", "open",
    ]
    assert "--limit" in args
    assert "--json" in args
    json_fields = args[args.index("--json") + 1]
    # The bash form requests the same three fields. Drift here would
    # silently break label filtering or selection ordering.
    assert "number" in json_fields
    assert "title" in json_fields
    assert "labels" in json_fields
