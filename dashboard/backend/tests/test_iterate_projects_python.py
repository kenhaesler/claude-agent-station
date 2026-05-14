"""End-to-end mock of iterate_projects with all external boundaries mocked (issue #383)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch


def test_iterate_projects_calls_each_phase_when_manager_produces_verdicts(
    tmp_path, monkeypatch,
):
    """Happy path: manager wrote verdicts → exit_code=0, every phase fires."""
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"name": "owner/repo", "enabled": True, "base_branch": "main"}],
        "limits": {"max_concurrent_employees": 1},
        "models": {"manager": "claude-sonnet-4-6"},
    }))

    call_log: list[str] = []

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: call_log.append("preflight"))
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: call_log.append("purge"))
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: call_log.append("resume"))
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace", lambda p, w: (call_log.append("workspace"), str(tmp_path))[1])

    async def _fake_orchestrate(project, config, run_id, workspaces_dir):
        call_log.append("orchestrate_project")
        # orchestrate_project returns (exit_code, stream_state) — None state
        # is the documented value when the orchestrator session never ran.
        return 0, None

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    # Happy-path mock: manager produced a verdicts file with no verdicts.
    # Returning a truthy-but-empty payload distinguishes "manager ran but had
    # nothing to verdict" from "manager never ran" (which #390 now flags
    # with exit_code=6).
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: (call_log.append("read_verdicts_file"), {"verdicts": []})[1],
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: call_log.append("digest") or "")

    rc, last_state = pl.iterate_projects("run-test", str(cfg), str(tmp_path))

    assert rc == 0
    assert last_state is None
    assert call_log == [
        "preflight",
        "purge",
        "resume",
        "workspace",
        "orchestrate_project",
        "read_verdicts_file",
        "digest",
    ], f"Unexpected phase order: {call_log}"


def test_iterate_projects_flags_missing_verdicts_file(tmp_path, monkeypatch):
    """When the manager produced no verdicts file at all, iterate_projects
    must surface it visibly: log ERROR, bump per-project exit_code to 6,
    record an ERROR result, attempt the manager_no_verdicts webhook emit.

    Pre-#390-review the missing-file path silently degraded with verdicts=[],
    losing all signal that the run did work but couldn't action it. This
    test pins the new fail-loud contract.
    """
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"name": "owner/repo", "enabled": True, "base_branch": "main"}],
        "limits": {"max_concurrent_employees": 1},
        "models": {"manager": "claude-sonnet-4-6"},
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace", lambda p, w: str(tmp_path))

    async def _fake_orchestrate(project, config, run_id, workspaces_dir):
        return 0, None

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    # No verdicts file: simulate manager crash / max-turns / never-spawned.
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    rc, _ = pl.iterate_projects("run-test", str(cfg), str(tmp_path))
    assert rc == 6, "missing verdicts file must bump exit_code to 6"


def test_iterate_projects_does_not_spawn_bash(tmp_path, monkeypatch):
    """The post-#383 iterate_projects must NEVER invoke run-manager.sh."""
    import inspect
    from agent import project_loop as pl

    src = inspect.getsource(pl)
    assert "run-manager.sh" not in src, "iterate_projects must not reference run-manager.sh"
    assert "subprocess.Popen" not in src or "popen" not in src.lower(), (
        "iterate_projects must not Popen any bash child"
    )


def test_run_manager_sh_is_deleted():
    """Issue #383: the bash file is removed from the tree."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    assert not (repo_root / "agent" / "scripts" / "run-manager.sh").exists(), (
        "agent/scripts/run-manager.sh must be deleted (issue #383)"
    )
