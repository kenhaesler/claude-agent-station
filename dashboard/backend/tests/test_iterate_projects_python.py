"""End-to-end mock of iterate_projects with all external boundaries mocked (issue #383)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch


def test_iterate_projects_calls_each_phase(tmp_path, monkeypatch):
    """Run iterate_projects against a sandbox config; assert every phase fires."""
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
    # #390: _read_verdicts_file replaces run_manager_review; mock it to
    # return None so the loop degrades gracefully (no verdicts to execute).
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: (call_log.append("read_verdicts_file"), None)[1],
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: call_log.append("digest") or "")

    rc, last_state = pl.iterate_projects("run-test", str(cfg), str(tmp_path))

    assert rc == 0
    assert last_state is None  # fake returned None state
    assert call_log == [
        "preflight",
        "purge",
        "resume",
        "workspace",
        "orchestrate_project",
        "read_verdicts_file",
        "digest",
    ], f"Unexpected phase order: {call_log}"


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
