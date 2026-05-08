"""Smoke tests for Auto Mode wiring in agent/station_orchestrator.py — ADR-0001.

These do not boot the Claude Agent SDK. They verify the module-level plumbing:
- orchestrator imports AutonomyLevel and make_audited_policy
- _coerce_level maps common project-config values to the right enum
- make_audited_policy returns an awaitable that consults policy_decide
"""

from __future__ import annotations

import inspect

import pytest

from agent import station_orchestrator
from agent.audit_hook import make_audited_policy
from agent.auto_mode import AutonomyLevel, _coerce_level
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def test_orchestrator_exposes_auto_mode_imports():
    """Regression guard: the orchestrator must import the policy + hook."""
    assert station_orchestrator.make_audited_policy is make_audited_policy
    assert station_orchestrator.AutonomyLevel is AutonomyLevel
    assert station_orchestrator._coerce_level is _coerce_level


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, AutonomyLevel.ASSISTED),
        ("", AutonomyLevel.ASSISTED),
        ("manual", AutonomyLevel.MANUAL),
        ("MANUAL", AutonomyLevel.MANUAL),
        ("assisted", AutonomyLevel.ASSISTED),
        ("auto", AutonomyLevel.AUTO),
        ("nonsense", AutonomyLevel.ASSISTED),
    ],
)
def test_coerce_level_via_orchestrator(raw, expected):
    assert station_orchestrator._coerce_level(raw) is expected


async def test_wired_policy_denies_push_to_main_regardless_of_level(tmp_path):
    db = tmp_path / "wire.db"
    # No schema init — best-effort audit should swallow the error.

    for level in AutonomyLevel:
        policy = make_audited_policy(
            run_id=f"run-wire-{level.value}",
            level=level,
            db_path=str(db),
        )
        decision = await policy("Bash", {"command": "git push origin main"}, None)
        assert isinstance(decision, PermissionResultDeny), (
            f"push to main must be denied at {level.value}, got {decision}"
        )


async def test_wired_policy_allows_read_at_all_levels(tmp_path):
    db = tmp_path / "wire.db"

    for level in AutonomyLevel:
        policy = make_audited_policy(
            run_id=f"run-r-{level.value}",
            level=level,
            db_path=str(db),
        )
        decision = await policy("Read", {"file_path": "/etc/hosts"}, None)
        assert isinstance(decision, PermissionResultAllow)


def test_orchestrator_options_block_contains_can_use_tool():
    """The source must wire can_use_tool=make_audited_policy(...) into
    ClaudeAgentOptions — otherwise the policy + audit never run.

    This is a file-level string assertion rather than a runtime check: we
    don't want to boot the SDK just to validate the integration.
    """
    source = inspect.getsource(station_orchestrator)
    assert "can_use_tool=make_audited_policy" in source, (
        "ClaudeAgentOptions must pass can_use_tool=make_audited_policy(...)"
    )
    assert 'agent_id="lead"' in source


# --- The Bridge Phase 1: narration emission --------------------------------


def test_narration_emitted_for_text_before_tool_use(monkeypatch):
    """Lead text immediately preceding a tool_use must be posted as a
    narration webhook with kind='directive'. This is the headline behavior
    of "The Bridge" Phase 1.
    """
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

    from agent import station_orchestrator

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        station_orchestrator, "post_webhook",
        lambda _config, event, data=None: captured.append((event, data or {})),
    )

    msg = AssistantMessage(
        content=[
            TextBlock(text="Reading the config to find the dashboard port."),
            ToolUseBlock(id="tu_1", name="Read", input={"file_path": "/tmp/x"}),
        ],
        model="claude-sonnet-4-6",
    )
    station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-1")

    narrations = [d for e, d in captured if e == "narration"]
    assert len(narrations) == 1
    n = narrations[0]
    assert n["narration_kind"] == "directive"
    assert "config" in n["narration"]
    assert n["agent_name"] == "Lead"
    assert n["run_id"] == "run-run-narr-1"


def test_narration_flushed_when_no_tool_follows(monkeypatch):
    """A trailing text block (no tool after it) must still be flushed —
    otherwise the lead's final intent statement is silently dropped.
    """
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    from agent import station_orchestrator

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        station_orchestrator, "post_webhook",
        lambda _config, event, data=None: captured.append((event, data or {})),
    )

    msg = AssistantMessage(
        content=[TextBlock(text="Done. All teammates have reported.")],
        model="claude-sonnet-4-6",
    )
    station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-2")

    narrations = [d for e, d in captured if e == "narration"]
    assert len(narrations) == 1
    assert narrations[0]["narration_kind"] == "directive"
    assert "Done" in narrations[0]["narration"]


def test_narration_skips_pure_tool_use_without_preceding_text(monkeypatch):
    """A tool_use with no preceding text emits no narration — the prompt
    asks for one before each tool, but if the lead skips it we must not
    fabricate one.
    """
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    from agent import station_orchestrator

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        station_orchestrator, "post_webhook",
        lambda _config, event, data=None: captured.append((event, data or {})),
    )

    msg = AssistantMessage(
        content=[ToolUseBlock(id="tu_x", name="Bash", input={"command": "ls"})],
        model="claude-sonnet-4-6",
    )
    station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-3")

    narrations = [d for e, d in captured if e == "narration"]
    assert narrations == []


# --- Mission Control: dedicated control poll task --------------------------


async def test_control_poll_loop_drains_messages_continuously(tmp_path, monkeypatch):
    """The poll loop must drain the run_controls queue on its own cadence,
    independent of the SDK stream. This is the core hotfix — previously
    controls were only drained when the SDK yielded a message, which could
    be 30+ seconds during a long tool call.
    """
    import asyncio
    import sqlite3

    from agent import station_orchestrator, run_control

    # Build a minimal sqlite DB that the control drain can talk to.
    db = tmp_path / "poll.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE run_controls (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT, action TEXT, payload TEXT, requested_by TEXT,
          requested_at TEXT, consumed_at TEXT
        )
        """
    )
    conn.commit()
    monkeypatch.setenv("STATION_DB_PATH", str(db))

    # Silence the webhook — the poller would otherwise try to POST.
    monkeypatch.setattr(
        station_orchestrator, "post_webhook",
        lambda *_a, **_kw: None,
    )

    full_run_id = "run-poll-test"
    pending: list[str] = []
    flags = {"stop": False}

    task = asyncio.create_task(
        station_orchestrator._control_poll_loop(
            full_run_id, {}, pending, flags, interval=0.05,
        )
    )

    try:
        # Inject a message after the task has started.
        await asyncio.sleep(0.1)
        conn.execute(
            "INSERT INTO run_controls (run_id, action, payload, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (full_run_id, "message", '{"text": "hello from operator"}', "api"),
        )
        conn.commit()

        # The poll task runs every 50ms; give it a few ticks to pick up.
        for _ in range(20):
            if pending:
                break
            await asyncio.sleep(0.05)
        assert pending == ["hello from operator"], (
            f"poll loop failed to drain message queue; got {pending}"
        )

        # A stop row should latch flags['stop'] and exit the loop.
        conn.execute(
            "INSERT INTO run_controls (run_id, action, payload, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (full_run_id, "stop", None, "api"),
        )
        conn.commit()

        for _ in range(20):
            if flags["stop"]:
                break
            await asyncio.sleep(0.05)
        assert flags["stop"], "stop action failed to latch"

        # Loop self-exits when stop is latched (no cancel needed).
        await asyncio.wait_for(task, timeout=1.0)
    finally:
        run_control._paused_runs.clear()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        conn.close()


# --- Task 4: has_open_vision_proposals -----------------------------------


def test_has_open_vision_proposals_true_when_label_present(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals

    fake_stdout = '[{"number": 1, "labels": [{"name": "vision-suggested"}]}]'
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": fake_stdout, "stderr": ""}),
    )
    assert has_open_vision_proposals("x/y") is True


def test_has_open_vision_proposals_false_when_no_matches(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""}),
    )
    assert has_open_vision_proposals("x/y") is False


def test_has_open_vision_proposals_false_on_gh_failure(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"}),
    )
    assert has_open_vision_proposals("x/y") is False


# --- Task 5: dispatch_vision_bootstrap -----------------------------------


def test_dispatch_vision_bootstrap_returns_dispatched_on_200(monkeypatch):
    import httpx

    from agent.station_orchestrator import dispatch_vision_bootstrap

    class R:
        status_code = 200
        text = ""

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "tok")
    assert dispatch_vision_bootstrap(42) == "dispatched"


def test_dispatch_vision_bootstrap_returns_already_running_on_409(monkeypatch):
    import httpx

    from agent.station_orchestrator import dispatch_vision_bootstrap

    class R:
        status_code = 409
        text = "vision-analyst already running"

    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    assert dispatch_vision_bootstrap(42) == "already-running"


def test_dispatch_vision_bootstrap_falls_back_to_subprocess(monkeypatch):
    """When launcher is unreachable, spawn directly via subprocess."""
    import httpx

    from agent.station_orchestrator import dispatch_vision_bootstrap

    def boom(*a, **k):
        raise httpx.RequestError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    spawned = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, *a, **k: spawned.append(cmd) or type("P", (), {"pid": 1234})(),
    )
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    assert dispatch_vision_bootstrap(42) == "dispatched"
    assert spawned == [["python", "-m", "agent.vision_analyst", "--project-id", "42"]]


def test_dispatch_vision_bootstrap_falls_back_on_unexpected_status(monkeypatch):
    """Unexpected non-2xx, non-409 -> subprocess fallback (graceful degradation)."""
    import httpx
    from agent.station_orchestrator import dispatch_vision_bootstrap
    class R:
        status_code = 500
        text = "internal server error"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    spawned = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, *a, **k: spawned.append(cmd) or type("P", (), {"pid": 1234})(),
    )
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    assert dispatch_vision_bootstrap(42) == "dispatched"
    assert spawned == [["python", "-m", "agent.vision_analyst", "--project-id", "42"]]


# --- Task 6: handle_empty_backlog -------------------------------------------


def test_handle_empty_backlog_dispatches_when_vision_and_no_proposals(monkeypatch, tmp_path):
    """Trigger A: dispatches and reports skip_reason=bootstrap-dispatched."""
    from agent import station_orchestrator as so

    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n## Users\nu\n## End-state\ne\n## Non-goals\nn\n## Principles\npr\n## Horizons\nh\n## Anti-patterns\na\n")

    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    posted = []
    monkeypatch.setattr(so, "post_webhook", lambda cfg, ev, data: posted.append((ev, data)))

    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-bootstrap-dispatched"
    assert dispatched == [42]
    assert any(ev == "finished" and d.get("skip_reason") == skip_reason for ev, d in posted)


def test_handle_empty_backlog_no_vision(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()  # no docs/vision.md
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-no-vision"
    assert dispatched == []


def test_handle_empty_backlog_proposals_pending(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n")
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: True)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-proposals-pending"
    assert dispatched == []


def test_handle_empty_backlog_already_running(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n")
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: "already-running")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-bootstrap-already-running"


# --- Issue #268: orchestrator early-exit must emit run_complete ----------


@pytest.mark.asyncio
async def test_orchestrate_emits_finished_when_no_projects(monkeypatch, tmp_path):
    """Issue #268: when ``config.projects`` is empty, the orchestrator must
    still emit a terminal ``finished`` webhook so the dashboard's placeholder
    Run row can transition out of ``unknown``.
    """
    from agent import station_orchestrator as so

    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        so, "post_webhook",
        lambda _config, event, data=None: posted.append((event, data or {})),
    )

    exit_code = await so.orchestrate(
        config={"projects": []},
        run_id="empty-cfg-1",
        workspaces_dir=str(tmp_path),
    )

    assert exit_code == 0
    finished = [(ev, d) for ev, d in posted if ev == "finished"]
    assert len(finished) == 1, f"expected one finished event, got {posted}"
    _, data = finished[0]
    assert data["run_id"] == "run-empty-cfg-1"
    assert data["status"] == "completed"
    assert data["skip_reason"] == "no-projects-configured"
    # Issue #266: ``mode`` must not be hard-coded in the no-projects
    # webhook — there's no project context to derive it from. Either the
    # key is absent or its value is None.
    assert data.get("mode") in (None,), f"mode must be omitted/null, got {data.get('mode')!r}"


@pytest.mark.asyncio
async def test_orchestrate_emits_finished_when_all_projects_disabled(monkeypatch, tmp_path):
    """All projects ``enabled=False`` is functionally identical to no
    projects — must still emit a terminal webhook."""
    from agent import station_orchestrator as so

    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        so, "post_webhook",
        lambda _config, event, data=None: posted.append((event, data or {})),
    )

    config = {
        "projects": [
            {"repo": "x/a", "enabled": False},
            {"repo": "x/b", "enabled": False},
        ],
    }
    exit_code = await so.orchestrate(
        config=config,
        run_id="all-disabled-1",
        workspaces_dir=str(tmp_path),
    )

    assert exit_code == 0
    finished = [(ev, d) for ev, d in posted if ev == "finished"]
    assert len(finished) == 1
    assert finished[0][1]["status"] == "completed"
    assert finished[0][1]["skip_reason"] == "no-projects-configured"


@pytest.mark.asyncio
async def test_orchestrate_skips_disabled_projects_in_mixed_config(monkeypatch, tmp_path):
    """Issue #268 follow-up: a config that mixes enabled and disabled
    projects must process ONLY the enabled ones. Regressing this check
    silently runs disabled projects (which is precisely the asymmetry
    the original PR review caught).
    """
    from agent import station_orchestrator as so

    fetched: list[str] = []
    monkeypatch.setattr(
        so, "fetch_eligible_issues",
        lambda repo, _limit, _ws: fetched.append(repo) or [],
    )
    # Stub the empty-backlog branch so we don't dispatch vision work
    # during the test.
    monkeypatch.setattr(
        so, "handle_empty_backlog",
        lambda **_kw: "no-eligible-issues-no-vision",
    )
    monkeypatch.setattr(so, "post_webhook", lambda *_a, **_kw: None)

    config = {
        "projects": [
            {"repo": "x/enabled-a", "enabled": True},
            {"repo": "x/disabled-b", "enabled": False},
            {"repo": "x/enabled-c"},  # default enabled=True
        ],
    }
    exit_code = await so.orchestrate(
        config=config,
        run_id="mixed-1",
        workspaces_dir=str(tmp_path),
    )

    assert exit_code == 0
    assert fetched == ["x/enabled-a", "x/enabled-c"], (
        f"disabled project must not be processed; fetched={fetched}"
    )


# --- Issue #266: project-mode wiring ---------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "full"),
        ("", "full"),
        ("full", "full"),
        ("FULL", "full"),
        ("analyze", "analyze"),
        ("plan", "plan"),
        ("plan_only", "plan_only"),
        ("triage", "full"),  # out-of-scope mode coerced to full
        ("garbage", "full"),
    ],
)
def test_normalize_project_mode(raw, expected):
    """All four valid modes survive; everything else falls back to 'full'."""
    assert station_orchestrator._normalize_project_mode(raw) == expected


def test_build_mode_block_full_is_empty():
    """'full' must inject no block — that's the contract."""
    assert station_orchestrator.build_mode_block("full", "/tmp/ws") == ""


def test_build_mode_block_plan_is_empty():
    """'plan' has no spawn-prompt block; it's enforced at manager review."""
    assert station_orchestrator.build_mode_block("plan", "/tmp/ws") == ""


def test_build_mode_block_analyze_contains_strict_rules():
    block = station_orchestrator.build_mode_block("analyze", "/var/ws/repo")
    assert "ANALYZE_MODE" in block
    assert "READ-ONLY" in block
    # Must point at the analyze report file path inside the workspace.
    assert "/var/ws/repo/.claude-analyze-report-" in block
    # Must explicitly forbid branches and pushes.
    assert "Do NOT create a feature branch" in block


def test_build_mode_block_plan_only_contains_plan_path():
    block = station_orchestrator.build_mode_block("plan_only", "/var/ws/repo")
    assert "PLAN_ONLY_MODE" in block
    assert "PRE-IMPLEMENTATION GATE" in block
    # Must point at the plan output file path.
    assert "/var/ws/repo/.claude-employee-plan-" in block
    # Must explicitly forbid Step 4.
    assert "STOP" in block


def test_build_mode_block_plan_only_with_revision_includes_feedback():
    block = station_orchestrator.build_mode_block(
        "plan_only", "/ws", plan_revision_feedback="Add error handling for null inputs",
        prior_plan_path="/ws/.claude-employee-plan-0.json",
    )
    assert "PLAN_REVISION" in block
    assert "Add error handling for null inputs" in block
    assert "/ws/.claude-employee-plan-0.json" in block


def test_build_team_prompt_full_has_no_mode_section():
    """'full' team prompt does NOT contain ANALYZE_MODE / PLAN_ONLY_MODE blocks."""
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 1, "title": "t", "body": "b", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="full",
    )
    assert "ANALYZE_MODE" not in prompt
    assert "PLAN_ONLY_MODE" not in prompt
    # No Project Mode banner for full mode.
    assert "Project Mode:" not in prompt


def test_build_team_prompt_analyze_injects_block_and_banner():
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 1, "title": "t", "body": "b", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="analyze",
    )
    assert "ANALYZE_MODE" in prompt
    assert "Project Mode: ANALYZE" in prompt
    assert "PLAN_ONLY_MODE" not in prompt


def test_build_team_prompt_plan_only_injects_block_and_banner():
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 1, "title": "t", "body": "b", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="plan_only",
    )
    assert "PLAN_ONLY_MODE" in prompt
    assert "Project Mode: PLAN_ONLY" in prompt
    assert "ANALYZE_MODE" not in prompt


def test_build_team_prompt_plan_has_banner_but_no_extra_block():
    """'plan' mode needs an instruction banner so the lead enforces it,
    even though there is no ANALYZE_MODE/PLAN_ONLY_MODE spawn-prompt block.
    """
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 1, "title": "t", "body": "b", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="plan",
    )
    assert "Project Mode: PLAN" in prompt
    assert "ANALYZE_MODE" not in prompt
    assert "PLAN_ONLY_MODE" not in prompt


# --- Plan review gate (issue #266) -----------------------------------------


def test_plan_review_gate_approve_enqueues_full_run():
    from agent.plan_review_gate import (
        PlanVerdict, apply_plan_verdict, build_followup_queue_item,
        RUN_STATE_PLAN_APPROVED,
    )
    verdict = PlanVerdict(
        verdict="APPROVE_PLAN",
        employee_index=0,
        issue_number=42,
        plan_path="/ws/.claude-employee-plan-0.json",
        feedback="LGTM",
    )
    action = apply_plan_verdict(verdict, project_repo="x/y")
    assert action.kind == "enqueue_full_run"
    assert action.next_run_state == RUN_STATE_PLAN_APPROVED
    assert action.follow_up_context["approved_plan_path"] == "/ws/.claude-employee-plan-0.json"

    item = build_followup_queue_item(action)
    assert item["mode"] == "full"
    assert item["state"] == "pending"
    assert item["project_repo"] == "x/y"
    assert item["issue_number"] == 42
    import json
    ctx = json.loads(item["context"])
    assert ctx["approved_plan_path"] == "/ws/.claude-employee-plan-0.json"
    assert ctx["from_plan_only_run"] is True


def test_plan_review_gate_revise_within_budget_loops():
    from agent.plan_review_gate import (
        PlanVerdict, apply_plan_verdict, RUN_STATE_AWAITING_PLAN_REVIEW,
    )
    v = PlanVerdict(
        verdict="REVISE_PLAN",
        employee_index=0,
        issue_number=42,
        plan_path="/ws/.claude-employee-plan-0.json",
        feedback="Add more detail on error handling",
    )
    action = apply_plan_verdict(v, project_repo="x/y", revision_count=0)
    assert action.kind == "revise"
    assert action.next_run_state == RUN_STATE_AWAITING_PLAN_REVIEW
    assert action.follow_up_context["plan_revision_feedback"] == "Add more detail on error handling"
    assert action.follow_up_context["revision_count"] == 1


def test_plan_review_gate_revise_past_budget_halts(monkeypatch):
    from agent.plan_review_gate import (
        PlanVerdict, apply_plan_verdict, RUN_STATE_PLAN_REJECTED,
    )
    monkeypatch.setenv("STATION_PLAN_REVISION_MAX", "2")
    v = PlanVerdict(
        verdict="REVISE_PLAN",
        employee_index=0,
        issue_number=42,
        plan_path="/ws/.claude-employee-plan-0.json",
        feedback="still no good",
    )
    action = apply_plan_verdict(v, project_repo="x/y", revision_count=2)
    assert action.kind == "halt_revisions_exhausted"
    assert action.next_run_state == RUN_STATE_PLAN_REJECTED


def test_plan_review_gate_reject_no_followup():
    from agent.plan_review_gate import (
        PlanVerdict, apply_plan_verdict, build_followup_queue_item,
        RUN_STATE_PLAN_REJECTED,
    )
    v = PlanVerdict(
        verdict="REJECT_PLAN",
        employee_index=0,
        issue_number=42,
        plan_path=None,
        feedback="issue is not viable",
    )
    action = apply_plan_verdict(v, project_repo="x/y")
    assert action.kind == "reject"
    assert action.next_run_state == RUN_STATE_PLAN_REJECTED
    # build_followup_queue_item refuses to build for non-enqueue actions.
    import pytest as _pt
    with _pt.raises(ValueError):
        build_followup_queue_item(action)


def test_parse_plan_verdicts_reads_manager_output(tmp_path):
    """parse_plan_verdicts handles the schema in REPORT-SCHEMAS.md."""
    from agent.plan_review_gate import parse_plan_verdicts
    f = tmp_path / "verdicts.json"
    f.write_text("""{
      "run_id": "run-1",
      "plan_verdicts": [
        {"verdict": "APPROVE_PLAN", "employee_index": 0, "issue_number": 42,
         "plan_quality_score": 90, "feedback": "ok",
         "plan_path": "/ws/.claude-employee-plan-0.json"}
      ]
    }""")
    rows = parse_plan_verdicts(f)
    assert len(rows) == 1
    assert rows[0].verdict == "APPROVE_PLAN"
    assert rows[0].issue_number == 42
    assert rows[0].plan_quality_score == 90


def test_parse_plan_verdicts_skips_unknown_verdict(tmp_path):
    from agent.plan_review_gate import parse_plan_verdicts
    f = tmp_path / "verdicts.json"
    f.write_text("""{
      "plan_verdicts": [
        {"verdict": "GARBAGE", "employee_index": 0},
        {"verdict": "REJECT_PLAN", "employee_index": 1, "issue_number": 7,
         "feedback": "bad"}
      ]
    }""")
    rows = parse_plan_verdicts(f)
    assert len(rows) == 1
    assert rows[0].verdict == "REJECT_PLAN"


def test_parse_plan_verdicts_missing_file_returns_empty(tmp_path):
    from agent.plan_review_gate import parse_plan_verdicts
    assert parse_plan_verdicts(tmp_path / "nope.json") == []


def test_get_plan_revision_max_default():
    from agent.plan_review_gate import (
        get_plan_revision_max, DEFAULT_PLAN_REVISION_MAX,
    )
    import os
    os.environ.pop("STATION_PLAN_REVISION_MAX", None)
    assert get_plan_revision_max() == DEFAULT_PLAN_REVISION_MAX
    assert DEFAULT_PLAN_REVISION_MAX == 2


def test_get_plan_revision_max_env_override(monkeypatch):
    from agent.plan_review_gate import get_plan_revision_max
    monkeypatch.setenv("STATION_PLAN_REVISION_MAX", "5")
    assert get_plan_revision_max() == 5


def test_get_plan_revision_max_invalid_falls_back(monkeypatch):
    from agent.plan_review_gate import (
        get_plan_revision_max, DEFAULT_PLAN_REVISION_MAX,
    )
    monkeypatch.setenv("STATION_PLAN_REVISION_MAX", "garbage")
    assert get_plan_revision_max() == DEFAULT_PLAN_REVISION_MAX


# --- Manager review package: MODE: header (issue #266) ---------------------


def test_run_manager_emits_mode_headers():
    """Verify run-manager.sh emits MODE: ANALYZE / MODE: PLAN / MODE: PLAN_REVIEW
    headers in the review package per project mode. This is a string-level
    assertion against the shell script — we don't actually run the script
    in tests because it's a long pipeline.
    """
    import pathlib
    script = pathlib.Path(__file__).parents[3] / "agent" / "scripts" / "run-manager.sh"
    text = script.read_text()
    # Each header must be emitted with the exact substring that manager.md
    # detects (lines 26-31).
    assert 'echo "MODE: ANALYZE"' in text
    assert 'echo "MODE: PLAN"' in text
    assert 'echo "MODE: PLAN_REVIEW"' in text
    # And only the analyze-mode banner emoji line was present before this
    # change — guard against accidental removal.
    assert 'project_mode" = "plan_only"' in text


# --- Frontend dropdown values match backend (issue #266) -------------------


def test_frontend_dropdown_values_match_backend_modes():
    """Smoke test: the four <option value="..."> entries in ProjectDetail
    and ProjectsPage must match VALID_PROJECT_MODES on the backend.
    Drift in either direction is a defect.
    """
    import pathlib
    valid = set(station_orchestrator.VALID_PROJECT_MODES)

    pd = pathlib.Path(__file__).parents[3] / "dashboard" / "frontend" / "src" / "pages" / "ProjectDetail.svelte"
    pp = pathlib.Path(__file__).parents[3] / "dashboard" / "frontend" / "src" / "pages" / "ProjectsPage.svelte"
    pd_text = pd.read_text()
    pp_text = pp.read_text()

    for mode in valid:
        assert f'value="{mode}"' in pd_text, f"ProjectDetail missing dropdown option for mode {mode}"
        assert f'value="{mode}"' in pp_text, f"ProjectsPage missing dropdown option for mode {mode}"

    # ProjectDetail must have inline help text under the dropdown.
    # We assert the existence of one short phrase per mode.
    assert "Read-only investigation" in pd_text  # analyze
    assert "Pre-implementation gate" in pd_text  # plan_only
    assert "Plan-quality output" in pd_text  # plan
    assert "Plan and implement" in pd_text  # full
