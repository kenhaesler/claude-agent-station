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


def test_iterate_projects_passes_workspace_and_dev_branch_to_executor(
    tmp_path, monkeypatch,
):
    """Regression: ``execute_approve_integration`` (and APPROVE / PR)
    require ``workspace`` as a keyword-only arg with no default. Before
    this fix, ``project_loop.execute_verdict`` was called with only
    ``run_id``, so every APPROVE_INTEGRATION verdict raised TypeError
    inside the iterate_projects loop. The TypeError propagated to
    RunDriver's catch-all, which marked the run failed and clobbered
    telemetry — silently, with no PR opened despite the manager having
    approved integration.

    Live evidence: run-20260515T235612Z generated 5x APPROVE_INTEGRATION
    verdicts in the verdicts file, but zero PRs appeared on the target
    repo, and the runs row ended with ``status=failed`` after the
    container exited at 30 minutes (well past the verdict step at
    ~22 minutes).
    """
    from pathlib import Path
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"repo": "owner/repo", "enabled": True, "branch": "main"}],
        "limits": {"max_concurrent_employees": 1},
        "integration": {"dev_branch": "autonomous/dev"},
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace",
        lambda p, w: str(tmp_path / "ws"),
    )

    async def _fake_orchestrate(project, config, run_id, workspaces_dir):
        return 0, None

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: {
            "verdicts": [
                {
                    "project": "owner/repo",
                    "issue_number": 17,
                    "verdict": "APPROVE_INTEGRATION",
                    "branch": "autonomous/issue-17",
                    "base_branch": "main",
                    "reasoning": "tests pass",
                },
            ],
        },
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    captured: dict = {}

    def _fake_execute(verdict, **kwargs):
        captured["verdict"] = verdict
        captured["kwargs"] = kwargs
        from agent.verdict_execution import ExecutionResult
        return ExecutionResult(
            verdict=verdict.verdict, project=verdict.project,
            issue_number=verdict.issue_number, success=True,
        )

    monkeypatch.setattr("agent.verdict_execution.execute", _fake_execute)

    rc, _ = pl.iterate_projects("run-tested", str(cfg), str(tmp_path))

    assert rc == 0, "happy-path verdict execution must not bump exit_code"
    assert captured, "execute_verdict was never invoked"
    kwargs = captured["kwargs"]
    assert "workspace" in kwargs, (
        f"execute_verdict must receive workspace=Path(...); got {list(kwargs)}"
    )
    assert isinstance(kwargs["workspace"], Path), (
        f"workspace must be a Path; got {type(kwargs['workspace'])}"
    )
    assert kwargs.get("dev_branch") == "autonomous/dev", (
        f"dev_branch must flow from config['integration']['dev_branch']; "
        f"got {kwargs.get('dev_branch')!r}"
    )
    assert kwargs.get("run_id") == "run-tested"


def test_iterate_projects_swallows_executor_exceptions_per_verdict(
    tmp_path, monkeypatch,
):
    """One executor blowing up MUST NOT cancel the rest of the verdict
    queue or kill the run. The pre-fix behavior let a TypeError on the
    first APPROVE_INTEGRATION verdict propagate all the way out of
    iterate_projects, marking the entire run failed and skipping every
    remaining verdict. Per-verdict try/except keeps the queue draining.
    """
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"repo": "owner/repo", "enabled": True, "branch": "main"}],
        "integration": {"dev_branch": "autonomous/dev"},
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda p, w: str(tmp_path / "ws"),
    )

    async def _fake_orchestrate(*a, **k):
        return 0, None

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: {
            "verdicts": [
                {"project": "owner/repo", "issue_number": 1, "verdict": "APPROVE_INTEGRATION", "branch": "b1"},
                {"project": "owner/repo", "issue_number": 2, "verdict": "APPROVE_INTEGRATION", "branch": "b2"},
            ],
        },
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    seen: list[int] = []

    def _flaky_execute(verdict, **kwargs):
        seen.append(verdict.issue_number)
        if verdict.issue_number == 1:
            raise RuntimeError("simulated executor crash")
        from agent.verdict_execution import ExecutionResult
        return ExecutionResult(verdict=verdict.verdict, project=verdict.project,
                               issue_number=verdict.issue_number, success=True)

    monkeypatch.setattr("agent.verdict_execution.execute", _flaky_execute)

    rc, _ = pl.iterate_projects("run-flaky", str(cfg), str(tmp_path))

    assert seen == [1, 2], (
        f"loop must continue past the crash; saw issue numbers {seen}"
    )
    assert rc != 0, "executor failure should bump exit_code"
