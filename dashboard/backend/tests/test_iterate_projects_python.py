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
        # orchestrate_project returns (exit_code, stream_state, work_attempted).
        # None stream_state is the documented value when the session never ran.
        return 0, None, True

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

    rc, last_state, _hint = pl.iterate_projects("run-test", str(cfg), str(tmp_path))

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
        return 0, None, True

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    # No verdicts file: simulate manager crash / max-turns / never-spawned.
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    rc, _, _hint = pl.iterate_projects("run-test", str(cfg), str(tmp_path))
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
        return 0, None, True

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

    rc, _, _hint = pl.iterate_projects("run-tested", str(cfg), str(tmp_path))

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


def test_iterate_projects_emits_manager_no_verdicts_with_kwargs(
    tmp_path, monkeypatch,
):
    """Issue #444: when the manager produces no verdicts file, project_loop
    emits a ``manager_no_verdicts`` webhook so the dashboard can surface it.

    The emitter signature is::

        emit(event: str, *, run_id: str, payload: dict | None = None)

    where ``run_id`` and ``payload`` are keyword-only. Pre-fix the call
    passed the payload as a positional second arg → TypeError, swallowed
    by a bare ``except Exception: logger.warning(...)``, so the webhook
    was never actually sent and the dashboard never learned about the
    failure. This test pins the call signature.
    """
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"repo": "owner/repo", "enabled": True, "branch": "main"}],
        "limits": {"max_concurrent_employees": 1},
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda p, w: str(tmp_path / "ws"),
    )

    async def _fake_orchestrate(*a, **k):
        return 0, None, True

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    # Missing verdicts file → manager_no_verdicts branch fires.
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file", lambda *a, **k: None,
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    captured: dict = {}

    def _fake_emit(event, *args, **kwargs):
        # Record both args + kwargs so we can detect any positional-arg
        # regression. A correct call has no positional args beyond ``event``.
        captured["event"] = event
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("agent.webhook_emitter.emit", _fake_emit)

    rc, _, _hint = pl.iterate_projects("test-run", str(cfg), str(tmp_path))

    assert rc == 6, "missing verdicts file must bump exit_code to 6"
    assert captured, (
        "manager_no_verdicts webhook was never emitted — the lazy "
        "import inside project_loop may have been refactored, or the "
        "branch is not firing"
    )
    assert captured["event"] == "manager_no_verdicts"
    assert captured["args"] == (), (
        "payload must be passed as a kwarg, not positional. "
        f"Got positional args: {captured['args']!r}"
    )
    assert captured["kwargs"].get("run_id") == "run-test-run", (
        f"run_id must be passed as kwarg and prefixed with 'run-'; "
        f"got {captured['kwargs'].get('run_id')!r}"
    )
    payload = captured["kwargs"].get("payload")
    assert payload is not None, "payload kwarg missing"
    assert payload.get("project") == "owner/repo"
    assert "verdicts_path" in payload
    assert payload["verdicts_path"].endswith("run-test-run-verdicts.json")
    # run_id must NOT be duplicated inside the payload — the wire body
    # builder in webhook_emitter.emit adds it at the top level.
    assert "run_id" not in payload, (
        "run_id should not be duplicated inside payload; "
        "webhook_emitter.emit already places it at the top level"
    )


def test_iterate_projects_skips_downstream_phases_when_no_verdicts(
    tmp_path, monkeypatch,
):
    """Issue #444 follow-up: when the manager produces no verdicts file,
    project_loop must bail out of the per-project loop body with an
    explicit ``continue`` — not fall through to plan_review_gate /
    verdict_execution with an empty list.

    Pre-fix the failure path fell through, which was harmless today
    (raw_verdicts became []) but masked the failure intent. A future
    downstream phase that assumed ``verdicts_payload`` was non-None
    would trip on the half-failed iteration. This test pins the
    "fail this project, move on" contract by driving a ``plan_only``
    project with no verdicts file and asserting that
    ``apply_plan_review_gate`` is never invoked.
    """
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{
            "repo": "owner/repo",
            "enabled": True,
            "branch": "main",
            "mode": "plan_only",
        }],
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda p, w: str(tmp_path / "ws"),
    )

    async def _fake_orchestrate(*a, **k):
        return 0, None, True

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    # No verdicts file → no-verdicts branch must fire.
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file", lambda *a, **k: None,
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **k: None)

    gate_calls: list[dict] = []

    def _spy_gate(**kwargs):
        gate_calls.append(kwargs)

    monkeypatch.setattr("agent.plan_review_gate.apply_plan_review_gate", _spy_gate)

    exec_calls: list = []

    def _spy_execute(verdict, **kwargs):
        exec_calls.append(verdict)
        from agent.verdict_execution import ExecutionResult
        return ExecutionResult(verdict=verdict.verdict, project=verdict.project,
                               issue_number=verdict.issue_number, success=True)

    monkeypatch.setattr("agent.verdict_execution.execute", _spy_execute)

    rc, _, _hint = pl.iterate_projects("run-skip", str(cfg), str(tmp_path))

    assert rc == 6, "missing verdicts file must bump exit_code to 6"
    assert gate_calls == [], (
        "plan_review_gate must NOT run when the manager produced no "
        f"verdicts file; got {len(gate_calls)} call(s): {gate_calls!r}"
    )
    assert exec_calls == [], (
        "verdict_execution must NOT run when there are no verdicts; "
        f"got {len(exec_calls)} call(s)"
    )


def test_iterate_projects_emits_plan_review_start_with_kwargs(
    tmp_path, monkeypatch,
):
    """Issue #444 audit: the ``plan_review_start`` emit site in
    iterate_projects shares the same call shape as ``manager_no_verdicts``
    and could regress in the same way. Pin its signature too.

    Note: the call site uses ``logger.exception`` (post-#444) — so a
    signature regression here would at least leave a traceback, but the
    webhook would still not fire. This test enforces the wire contract.
    """
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{
            "repo": "owner/repo",
            "enabled": True,
            "branch": "main",
            "mode": "plan_only",
        }],
    }))

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda p, w: str(tmp_path / "ws"),
    )

    async def _fake_orchestrate(*a, **k):
        return 0, None, True

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: {"verdicts": []},
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")
    monkeypatch.setattr(
        "agent.plan_review_gate.apply_plan_review_gate", lambda **kw: None,
    )

    captured: list[dict] = []

    def _fake_emit(event, *args, **kwargs):
        captured.append({"event": event, "args": args, "kwargs": kwargs})

    monkeypatch.setattr("agent.webhook_emitter.emit", _fake_emit)

    pl.iterate_projects("plan-run", str(cfg), str(tmp_path))

    plan_starts = [c for c in captured if c["event"] == "plan_review_start"]
    assert plan_starts, (
        f"plan_review_start was never emitted; observed events: "
        f"{[c['event'] for c in captured]!r}"
    )
    call = plan_starts[0]
    assert call["args"] == (), (
        f"plan_review_start payload must be a kwarg, not positional; "
        f"got positional args: {call['args']!r}"
    )
    assert call["kwargs"].get("run_id") == "run-plan-run"
    payload = call["kwargs"].get("payload")
    assert payload is not None and payload.get("project") == "owner/repo"
    assert payload.get("mode") == "plan_only"


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
        return 0, None, True

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

    rc, _, _hint = pl.iterate_projects("run-flaky", str(cfg), str(tmp_path))

    assert seen == [1, 2], (
        f"loop must continue past the crash; saw issue numbers {seen}"
    )
    assert rc != 0, "executor failure should bump exit_code"


def test_iterate_projects_emits_project_skipped_no_work_when_no_work_attempted(
    tmp_path, monkeypatch
):
    """Idle case: orchestrate_project returns work_attempted=False →
    iterate_projects emits project_skipped_no_work, NOT
    manager_no_verdicts, and does not bump exit_code.

    Spec: docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md
    Issues: #446 #447
    """
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"test/repo","enabled":true,"mode":"full"}]}')
    workspaces_dir = str(tmp_path / "workspaces")

    captured_emits: list[tuple] = []

    def fake_emit(event, *, run_id, payload=None):
        captured_emits.append((event, run_id, payload))

    async def fake_orchestrate_async(*args, **kwargs):
        return (0, None, False)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate_async
    )
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", fake_emit)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    exit_code, _last_state, _terminal_hint = project_loop.iterate_projects(
        "test-run", str(config_path), workspaces_dir
    )

    emit_names = [e[0] for e in captured_emits]
    assert "project_skipped_no_work" in emit_names, (
        f"Expected project_skipped_no_work emit, got: {emit_names}"
    )
    assert "manager_no_verdicts" not in emit_names, (
        f"manager_no_verdicts must NOT fire in idle case, got: {emit_names}"
    )

    skip_event = next(e for e in captured_emits if e[0] == "project_skipped_no_work")
    assert skip_event[1] == "run-test-run", f"run_id wrong: {skip_event[1]}"
    assert skip_event[2] is not None
    assert skip_event[2].get("project") == "test/repo"
    assert skip_event[2].get("reason") == "no_eligible_work"

    assert exit_code == 0


def test_iterate_projects_still_emits_manager_no_verdicts_for_real_failure(
    tmp_path, monkeypatch
):
    """Regression pin: work_attempted=True + verdicts file missing must
    still trigger the existing manager_no_verdicts path (exit_code=6).
    The work_attempted discriminator must not suppress real failures.
    """
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"test/repo","enabled":true,"mode":"full"}]}')
    workspaces_dir = str(tmp_path / "workspaces")

    captured_emits: list[tuple] = []

    def fake_emit(event, *, run_id, payload=None):
        captured_emits.append((event, run_id, payload))

    async def fake_orchestrate(*a, **kw):
        return (0, None, True)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file", lambda p: None
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", fake_emit)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    exit_code, _last_state, _terminal_hint = project_loop.iterate_projects(
        "test-run", str(config_path), workspaces_dir
    )

    emit_names = [e[0] for e in captured_emits]
    assert "manager_no_verdicts" in emit_names
    assert "project_skipped_no_work" not in emit_names
    assert exit_code == 6


def test_iterate_projects_returns_3tuple_on_preflight_failure(tmp_path, monkeypatch):
    """Regression: preflight failure path must return a 3-tuple so
    RunDriver.run's unpack doesn't crash. #446 #447."""
    from agent import project_loop
    from agent.preflight import PreflightError

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"x/x","enabled":true}]}')

    def boom(*a, **kw):
        raise PreflightError("simulated")

    monkeypatch.setattr("agent.preflight.run_preflight", boom)

    result = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "ws")
    )
    assert len(result) == 3, f"expected 3-tuple, got {len(result)}-tuple"
    exit_code, _last_state, terminal_status_hint = result
    assert exit_code == 2
    assert terminal_status_hint is None


def test_iterate_projects_returns_skipped_hint_when_all_projects_idle(
    tmp_path, monkeypatch
):
    """Run-level: all projects idle, no failures → terminal_status_hint='skipped'."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    async def fake_orchestrate(*a, **kw):
        return (0, None, False)  # both projects idle

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    # Match the monkeypatch targets used in pre-existing tests in this file
    # (Task 2 implementer found that the plan's lazy-import targets don't
    # exist as module attributes; use the canonical module paths instead).
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code == 0
    assert terminal_status_hint == "skipped"


def test_iterate_projects_no_skipped_hint_when_any_project_did_work(
    tmp_path, monkeypatch
):
    """Mixed: one idle, one did work → no skipped hint (run is completed)."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    call_count = {"n": 0}

    async def fake_orchestrate(*a, **kw):
        call_count["n"] += 1
        # First call: idle; second: did work
        return (0, None, call_count["n"] != 1)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    # Second project's verdicts file present, empty verdicts (happy minimal).
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda p: {"verdicts": []},
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code == 0
    assert terminal_status_hint is None, (
        "Mixed idle+work runs must NOT be marked skipped"
    )


def test_iterate_projects_no_skipped_hint_when_any_real_failure(
    tmp_path, monkeypatch
):
    """Mixed: one idle, one fails → no skipped hint (run is failed)."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    call_count = {"n": 0}

    async def fake_orchestrate(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (0, None, False)   # idle
        raise RuntimeError("simulated project failure")

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.workspace_setup.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code != 0
    assert terminal_status_hint is None, (
        "Run with a real failure must NOT be marked skipped"
    )
