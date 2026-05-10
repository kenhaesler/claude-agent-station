"""Tests for __main__ helpers: conflict-region counting + handoff report.

Covers the load-bearing review-feedback fixes:
- Finding #2: advisory tier uses conflict-region line count, not file size.
- Finding #4: resolver writes a synthesized employee report so the existing
  manager-review pipeline picks up the resolution.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent.conflict_resolver.__main__ import (
    _conflict_region_line_count,
    _write_handoff_report,
)


def test_region_count_uses_only_conflict_lines(tmp_path: Path):
    """A 5000-line file with one tiny conflict should not over-count."""
    body = "\n".join([f"line {i}" for i in range(5000)])
    conflicted = (
        body
        + "\n<<<<<<< HEAD\nour\n=======\ntheir\n>>>>>>> base\n"
        + body
    )
    fp = tmp_path / "big.txt"
    fp.write_text(conflicted)
    # 1 line on each side of the marker = 2 lines total.
    assert _conflict_region_line_count(str(tmp_path), ["big.txt"]) == 2


def test_region_count_sums_across_files(tmp_path: Path):
    f1 = tmp_path / "a.txt"
    f1.write_text("<<<<<<< HEAD\nx\ny\n=======\nz\n>>>>>>> base\n")
    f2 = tmp_path / "b.txt"
    f2.write_text("<<<<<<< HEAD\np\n=======\nq\nr\ns\n>>>>>>> base\n")
    # a.txt: 2 ours + 1 theirs = 3. b.txt: 1 + 3 = 4. Total = 7.
    assert _conflict_region_line_count(str(tmp_path), ["a.txt", "b.txt"]) == 7


def test_region_count_skips_unreadable(tmp_path: Path):
    # Pointing at a non-existent path should yield 0, not crash.
    assert _conflict_region_line_count(str(tmp_path), ["does-not-exist.txt"]) == 0


def test_handoff_report_written_with_commits_and_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("base\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "feat"], cwd=repo, check=True)
    (repo / "b.txt").write_text("new\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: add b"], cwd=repo, check=True)

    _write_handoff_report(
        workspace=str(repo),
        branch="feat",
        base_branch="main",
        run_id="run-test-001",
    )

    report_path = repo / ".claude-employee-report-conflict-resolver.json"
    assert report_path.is_file()
    data = json.loads(report_path.read_text())
    assert data["status"] == "success"
    assert data["branch"] == "feat"
    assert data["base_branch"] == "main"
    assert data["files_changed"] == ["b.txt"]
    assert len(data["commits"]) == 1
    assert data["synthesized_by"] == "conflict-resolver"
    assert data["run_id"] == "run-test-001"


def test_handoff_report_handles_no_workspace(tmp_path: Path):
    """When git can't run (path doesn't exist), the writer logs but doesn't raise."""
    bogus = tmp_path / "does-not-exist"
    # Should not raise; the function is best-effort.
    _write_handoff_report(
        workspace=str(bogus),
        branch="feat",
        base_branch="main",
        run_id="run-x",
    )
