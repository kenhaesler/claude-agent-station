"""Tests for the ``python -m agent.build_review_package`` CLI.

The CLI exists so the lead can build the review package *before* it
spawns the manager sibling. Without it, the manager read a missing
review.md and silently picked up stale reports from a prior run — the
2026-05-21 LCM bug. These tests pin the contract the lead's prompt
depends on: given worktree paths, the CLI produces the same file the
in-process post-session helper produces, and emits the absolute path
on stdout so Bash can chain it.
"""

from __future__ import annotations

import json
from pathlib import Path


def _seed_worktree(wt: Path, role: str, branch: str, issues: list[int]) -> None:
    """Drop a minimal employee report into a worktree directory."""
    wt.mkdir(parents=True, exist_ok=True)
    (wt / f".claude-employee-report-{role}.json").write_text(
        json.dumps({
            "agent": role, "branch": branch, "issue_numbers": issues,
            "status": "complete",
        }),
        encoding="utf-8",
    )


def test_cli_writes_review_package_and_prints_path(
    tmp_path: Path, capsys,
):
    """Happy path: three worktrees, three reports, one review.md."""
    from agent import build_review_package

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    backend = tmp_path / "wt-backend"
    frontend = tmp_path / "wt-frontend"
    qa = tmp_path / "wt-qa"
    _seed_worktree(backend, "backend", "feature/x", [1, 2])
    _seed_worktree(frontend, "frontend", "feature/x-ui", [1])
    _seed_worktree(qa, "qa", "feature/x-tests", [1, 2])

    rc = build_review_package.main([
        "--run-id", "20260521T151955Z",
        "--log-dir", str(log_dir),
        "--workspaces", str(backend), str(frontend), str(qa),
    ])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert out == str(log_dir / "run-20260521T151955Z-review.md")
    body = (log_dir / "run-20260521T151955Z-review.md").read_text()
    assert "MODE: FULL" in body
    assert "feature/x" in body
    assert "feature/x-ui" in body
    assert "feature/x-tests" in body


def test_cli_is_idempotent_when_review_file_already_exists(
    tmp_path: Path, capsys,
):
    """Second invocation must not overwrite an already-populated file —
    matches the in-process helper's contract so the post-session
    finally-block call stays a cheap no-op."""
    from agent import build_review_package

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target = log_dir / "run-XYZ-review.md"
    target.write_text("USER_AUTHORED_CONTENT\n", encoding="utf-8")

    wt = tmp_path / "wt"
    _seed_worktree(wt, "backend", "feature/y", [3])

    rc = build_review_package.main([
        "--run-id", "XYZ", "--log-dir", str(log_dir),
        "--workspaces", str(wt),
    ])

    assert rc == 0
    assert capsys.readouterr().out.strip() == str(target)
    assert target.read_text() == "USER_AUTHORED_CONTENT\n"


def test_cli_help_does_not_import_sdk(tmp_path: Path, capsys):
    """``--help`` must be cheap. If a future refactor moves the heavy
    SDK import to module scope, this test fails."""
    from agent import build_review_package
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        build_review_package.main(["--help"])
    assert excinfo.value.code == 0
    assert "build_review_package" in capsys.readouterr().out
