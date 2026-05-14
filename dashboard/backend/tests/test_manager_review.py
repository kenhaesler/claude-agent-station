"""Tests for agent.manager_review (issue #383 bash port)."""
from __future__ import annotations

import json
import subprocess
import pytest
from unittest.mock import MagicMock


def test_happy_review_returns_verdicts(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("review package contents")

    # The Verdict dataclass uses field 'verdict', not 'decision'.
    fake_stdout = json.dumps({
        "verdicts": [
            {"project": "owner/repo", "issue_number": 1, "verdict": "APPROVE",
             "branch": "autonomous/issue-1", "base_branch": "main", "reasoning": "ok"},
        ],
    })
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")),
    )

    verdicts = manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "APPROVE"
    assert verdicts[0].issue_number == 1


def test_malformed_json_raises(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("x")
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")),
    )

    with pytest.raises(manager_review.ManagerReviewError, match="JSON"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})


def test_nonzero_exit_raises(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("x")
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")),
    )

    with pytest.raises(manager_review.ManagerReviewError, match="claude -p"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})


def test_empty_package_raises(tmp_path):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("")
    with pytest.raises(manager_review.ManagerReviewError, match="empty"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})
