"""Tests for agent.digest (issue #383 bash port)."""
from __future__ import annotations


def test_empty_run_digest(tmp_path):
    from agent.digest import write_digest
    path = write_digest(run_id="run-empty", results=[], log_dir=str(tmp_path))
    txt = (tmp_path / "run-empty-digest.md").read_text()
    assert "run-empty" in txt
    assert "No verdicts" in txt or "0 verdicts" in txt


def test_multi_verdict_digest(tmp_path):
    from agent.digest import write_digest
    results = [
        {"project": "owner/a", "issue_number": 1, "decision": "APPROVE", "branch": "autonomous/issue-1"},
        {"project": "owner/a", "issue_number": 2, "decision": "REJECT", "branch": "autonomous/issue-2", "reasoning": "tests fail"},
        {"project": "owner/b", "issue_number": 3, "decision": "PR", "branch": "autonomous/issue-3"},
    ]
    write_digest(run_id="run-multi", results=results, log_dir=str(tmp_path))
    txt = (tmp_path / "run-multi-digest.md").read_text()
    assert "owner/a" in txt and "owner/b" in txt
    assert "APPROVE" in txt and "REJECT" in txt and "PR" in txt


def test_verdict_with_error_recorded(tmp_path):
    from agent.digest import write_digest
    results = [
        {"project": "owner/a", "issue_number": 1, "decision": "ERROR", "error": "rebase failed"},
    ]
    write_digest(run_id="run-err", results=results, log_dir=str(tmp_path))
    txt = (tmp_path / "run-err-digest.md").read_text()
    assert "ERROR" in txt
    assert "rebase failed" in txt
