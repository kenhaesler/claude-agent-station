"""Tests for the cross-run stale-state cleanup in workspace_setup.

Background: the base workspace (``/var/lib/.../workspaces/<repo>``)
persists between runs while per-role worktrees are torn down post-run.
``_synthesize_employee_report`` writes role-named report files into
*the base workspace*, so prior-run reports sit there indefinitely. The
manager-sibling's mid-session fallback (when the per-run review.md
doesn't exist yet) used to glob those files and produce verdicts for
the wrong run. These tests pin the new behavior: every run start
deletes the prior-run state so the fallback path can't see it.
"""

from __future__ import annotations

from pathlib import Path


def _touch(p: Path, content: str = "{}") -> None:
    p.write_text(content, encoding="utf-8")


def test_clean_stale_manager_state_removes_known_files(tmp_path: Path):
    """Synthesized reports, the sidecar, and the legacy single-file
    report must all be deleted."""
    from agent.workspace_setup import _clean_stale_manager_state

    _touch(tmp_path / ".claude-employee-report-backend.json", '{"branch": "stale"}')
    _touch(tmp_path / ".claude-employee-report-frontend.json")
    _touch(tmp_path / ".claude-employee-report-qa.json")
    _touch(tmp_path / ".claude-employee-report-0.json")
    _touch(tmp_path / ".claude-employee-report.json")  # legacy single-file form
    _touch(tmp_path / ".claude-manager-paths.json", '{"verdicts_file": "old"}')

    _clean_stale_manager_state(tmp_path)

    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert leftovers == [], f"expected empty workspace, got {leftovers}"


def test_clean_stale_manager_state_preserves_unrelated_files(tmp_path: Path):
    """Source files, lockfiles, and other dotfiles must not be touched —
    only the manager-review state files."""
    from agent.workspace_setup import _clean_stale_manager_state

    _touch(tmp_path / "README.md", "# hi")
    _touch(tmp_path / ".gitignore", "node_modules/")
    _touch(tmp_path / ".claude-team-contracts.md", "# contracts")
    _touch(tmp_path / ".claude-run-mode", "full")
    _touch(tmp_path / ".claude-employee-report-backend.json", '{"branch": "stale"}')

    _clean_stale_manager_state(tmp_path)

    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert leftovers == [
        ".claude-run-mode",
        ".claude-team-contracts.md",
        ".gitignore",
        "README.md",
    ]


def test_clean_stale_manager_state_no_op_on_empty_workspace(tmp_path: Path):
    """Running on a fresh workspace must not raise or create anything."""
    from agent.workspace_setup import _clean_stale_manager_state

    _clean_stale_manager_state(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_clean_stale_manager_state_survives_unlink_error(
    tmp_path: Path, monkeypatch, caplog,
):
    """A locked or read-only file must produce a WARNING and let the
    cleanup continue — never raise into the orchestrator."""
    from agent import workspace_setup

    _touch(tmp_path / ".claude-employee-report-backend.json")
    _touch(tmp_path / ".claude-employee-report-frontend.json")

    real_unlink = Path.unlink
    calls: list[Path] = []

    def _raising_unlink(self, *args, **kwargs):  # noqa: ANN001
        calls.append(self)
        if "backend" in self.name:
            raise OSError("permission denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _raising_unlink)

    with caplog.at_level("WARNING", logger="agent.workspace_setup"):
        workspace_setup._clean_stale_manager_state(tmp_path)

    # Both files were attempted; the frontend one succeeded.
    assert len(calls) == 2
    assert (tmp_path / ".claude-employee-report-frontend.json").exists() is False
    # The failed unlink was logged.
    assert any(
        "could not remove stale" in rec.message
        and "backend" in rec.message
        for rec in caplog.records
    )
