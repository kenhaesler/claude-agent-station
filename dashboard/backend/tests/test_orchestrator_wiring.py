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


def test_orchestrator_writes_run_mode_marker():
    """Regression for run-20260509T183351Z: run-manager.sh's review-package
    builder needs the *effective* run mode (e.g. ``full`` when implementing
    from approved plans), not the project's static config mode (e.g.
    ``plan_only``). Pin the marker write in the orchestrator source so it
    can't be silently dropped — the bash side reads it via resolve_run_mode."""
    source = inspect.getsource(station_orchestrator)
    assert ".claude-run-mode" in source, (
        "orchestrator must write a per-run mode marker to the workspace"
    )
    # Marker must be written from the project iteration so it picks up the
    # post-queue-drain mode, not the pre-drain config mode.
    assert "f.write(project_mode)" in source


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


@pytest.mark.asyncio
async def test_narration_emitted_for_text_before_tool_use(monkeypatch):
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
    await station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-1")

    narrations = [d for e, d in captured if e == "narration"]
    assert len(narrations) == 1
    n = narrations[0]
    assert n["narration_kind"] == "directive"
    assert "config" in n["narration"]
    assert n["agent_name"] == "Lead"
    assert n["run_id"] == "run-run-narr-1"


@pytest.mark.asyncio
async def test_narration_flushed_when_no_tool_follows(monkeypatch):
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
    await station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-2")

    narrations = [d for e, d in captured if e == "narration"]
    assert len(narrations) == 1
    assert narrations[0]["narration_kind"] == "directive"
    assert "Done" in narrations[0]["narration"]


@pytest.mark.asyncio
async def test_narration_skips_pure_tool_use_without_preceding_text(monkeypatch):
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
    await station_orchestrator.handle_stream_event(msg, config={}, run_id="run-narr-3")

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

    # Stub the queue-drain helper added in #289 follow-up so this test
    # doesn't depend on a DB schema being present. The drain returning
    # ``[]`` matches the "no pre-approved work" branch and lets the
    # original fetch_eligible_issues flow run.
    async def _empty_claim(_repo, _run_id, **_kw):
        return []
    monkeypatch.setattr(so, "claim_pending_queue_items", _empty_claim)

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
#
# These three tests re-pin the contracts that were dropped along with
# ``run-manager.sh`` in PR #405 (issue #383). The bash-era tests asserted
# that the shell driver emitted the right ``MODE:`` headers, the
# ``plan_review_start`` webhook, and invoked ``python -m
# agent.plan_review_gate``. Those tests used ``read_text()`` on the now-
# deleted script. The behaviours still apply to the Python composition
# (issue #406): every project mode must surface in the manager's review
# package, ``plan_only`` projects must emit ``plan_review_start`` during
# the manager-review window, and ``apply_plan_review_gate`` must run for
# every ``plan_only`` project after verdicts are produced.


@pytest.mark.parametrize(
    "mode,expected_header",
    [
        ("full", "MODE: FULL"),
        ("analyze", "MODE: ANALYZE"),
        ("plan", "MODE: PLAN"),
        ("plan_only", "MODE: PLAN_ONLY"),
    ],
)
def test_review_package_emits_mode_header(tmp_path, mode, expected_header):
    """The manager review package must carry a ``MODE: <MODE>`` line so the
    manager-sibling agent can detect the project's resolved mode while
    composing its verdict. The bash era enforced this via ``echo "MODE: ..."``
    inside ``run-manager.sh``; the Python composition lives in
    :func:`agent.station_orchestrator._ensure_review_package`. We inspect
    the assembled package string directly rather than booting the SDK.
    Replaces ``test_run_manager_emits_mode_headers`` (deleted in PR #405).
    """
    from agent.station_orchestrator import _ensure_review_package

    # Synthesise a minimal workspace with one employee report so the
    # helper has real content to splice. The MODE: line is emitted up
    # front and is independent of report contents.
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "repo"
    repo.mkdir(parents=True)
    (repo / ".claude-employee-report-0.json").write_text(
        '{"issue_number":1,"verdict_request":"APPROVE","summary":"x"}',
        encoding="utf-8",
    )
    log_dir = tmp_path / "log"
    log_dir.mkdir()

    out = _ensure_review_package(
        run_id=f"mode-{mode}",
        log_dir=log_dir,
        workspaces=[repo],
        mode=mode,
    )
    text = out.read_text(encoding="utf-8")
    assert expected_header in text, (
        f"review package for mode={mode!r} missing {expected_header!r}; "
        f"got:\n{text}"
    )
    # And the header must appear on its own line (manager.md parses it
    # by line, not by substring) — guard against future "MODE: FULL extra"
    # drift.
    assert any(line.strip() == expected_header for line in text.splitlines()), (
        f"{expected_header} must appear as a standalone line"
    )


def test_iterate_projects_emits_plan_review_start_for_plan_only(
    tmp_path, monkeypatch,
):
    """Pinning #406 contract: a ``plan_only`` project drives a
    ``plan_review_start`` webhook emission so the dashboard banner flips
    to ``plan_reviewing`` during the manager-review window. We mock the
    webhook emitter and assert the event appears in the captured calls.
    Replaces ``test_run_manager_emits_plan_review_start_for_plan_only_projects``
    (deleted in PR #405).
    """
    import json
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{
            "repo": "owner/repo",
            "enabled": True,
            "branch": "main",
            "mode": "plan_only",
        }],
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

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", _fake_orchestrate,
    )
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: {"verdicts": []},
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "agent.webhook_emitter.emit",
        lambda event, *, run_id, payload=None: emitted.append(
            (event, {"run_id": run_id, **(payload or {})}),
        ),
    )

    pl.iterate_projects("run-plan-only", str(cfg), str(tmp_path))

    events = [e for e, _ in emitted]
    assert "plan_review_start" in events, (
        f"plan_only project must trigger plan_review_start emission; "
        f"saw events={events}"
    )


def test_iterate_projects_invokes_plan_review_gate_for_plan_only(
    tmp_path, monkeypatch,
):
    """Pinning #406 contract: a ``plan_only`` project must drive
    :func:`agent.plan_review_gate.apply_plan_review_gate` after manager
    verdicts have been read. We patch the gate function and assert it
    was called with the project mode, the verdicts path, repo, run id,
    and workspace. Replaces ``test_run_manager_invokes_plan_review_gate``
    (deleted in PR #405).
    """
    import json
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{
            "repo": "owner/repo",
            "enabled": True,
            "branch": "main",
            "mode": "plan_only",
        }],
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

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", _fake_orchestrate,
    )
    # Manager produced an empty (but present) verdicts payload — enough
    # for the gate to fire even without real verdicts.
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda *a, **k: {"verdicts": []},
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        "agent.plan_review_gate.apply_plan_review_gate",
        lambda **kwargs: gate_calls.append(kwargs) or [],
    )

    pl.iterate_projects("run-gate-test", str(cfg), str(tmp_path))

    assert gate_calls, (
        "apply_plan_review_gate was not invoked for the plan_only project"
    )
    call = gate_calls[0]
    assert call.get("project_mode") == "plan_only", (
        f"gate must be told the project's mode; got {call.get('project_mode')!r}"
    )
    assert call.get("project_repo") == "owner/repo", (
        f"gate must receive the project repo; got {call.get('project_repo')!r}"
    )
    # The gate's run_id arg is the full run id (with the ``run-`` prefix)
    # so the dashboard webhook handler can correlate. iterate_projects's
    # caller passes the bare id, so wiring must add the prefix back —
    # this is the same contract enforced by #432/#433.
    rid = call.get("run_id", "")
    # Tightened post-#432/#433: the gate's run_id arg MUST carry the
    # ``run-`` prefix so the dashboard webhook handler can correlate. A
    # loose substring check would silently accept ``gate-test`` even if
    # the wiring forgot to prefix.
    assert rid.startswith("run-"), (
        f"gate must receive a prefixed run id (post-#432/#433); got {rid!r}"
    )
    assert "gate-test" in rid, (
        f"gate must receive the run id seeded by the test; got {rid!r}"
    )


# --- Plan-review gate live wiring (issue #266 review feedback) -------------


def test_apply_plan_review_gate_skips_non_plan_only_modes(monkeypatch, tmp_path):
    """The gate is a no-op for full / analyze / plan — it must never POST
    when the project mode is not plan_only.
    """
    from agent import plan_review_gate as g

    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **k: posted.append(("queue", a)) or {"id": 999})
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **k: posted.append(("event", {"event": ev, "rid": rid})) or True)

    for mode in ("full", "analyze", "plan", "garbage"):
        outcomes = g.apply_plan_review_gate(
            project_mode=mode,
            verdicts_path=tmp_path / "doesnt-exist.json",
            project_repo="x/y",
            run_id="run-skip",
        )
        assert outcomes == []

    assert posted == [], f"Gate must not POST for non-plan_only modes, got {posted}"


def test_apply_plan_review_gate_approve_posts_queue_and_status(monkeypatch, tmp_path):
    """APPROVE_PLAN end-to-end: parse verdicts → POST /api/queue → POST
    plan_approved run-event.
    """
    import json
    from agent import plan_review_gate as g

    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps({
        "run_id": "run-1",
        "plan_verdicts": [
            {
                "project": "x/y", "verdict": "APPROVE_PLAN",
                "employee_index": 0, "issue_number": 42,
                "plan_path": "/ws/.claude-employee-plan-0.json",
                "plan_quality_score": 90, "feedback": "ok",
            }
        ],
    }))

    queue_calls: list[dict] = []
    event_calls: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(
        g, "post_queue_item",
        lambda payload, **kw: queue_calls.append(payload) or {"id": 7, "state": "pending"},
    )
    monkeypatch.setattr(
        g, "post_run_event",
        lambda ev, rid, **kw: event_calls.append((ev, rid, kw.get("extra") or {})) or True,
    )

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=verdicts_file,
        project_repo="x/y",
        run_id="run-1",
    )

    assert len(outcomes) == 1
    assert outcomes[0].action_kind == "enqueue_full_run"
    assert outcomes[0].queue_item_id == 7
    assert outcomes[0].posted_status_event is True

    # Queue payload shape
    assert len(queue_calls) == 1
    payload = queue_calls[0]
    assert payload["mode"] == "full"
    assert payload["state"] == "pending"
    assert payload["project_repo"] == "x/y"
    assert payload["issue_number"] == 42
    # context is JSON-encoded (single-encoded — schema column is str | None)
    assert isinstance(payload["context"], str)
    ctx = json.loads(payload["context"])
    assert ctx["approved_plan_path"] == "/ws/.claude-employee-plan-0.json"
    assert ctx["from_plan_only_run"] is True

    # Event sequence: awaiting_plan_review FIRST, then plan_approved
    events = [e[0] for e in event_calls]
    assert events == ["awaiting_plan_review", "plan_approved"]
    # Both events must reference the same run_id and mode
    for _ev, rid, extra in event_calls:
        assert rid == "run-1"
        assert extra["mode"] == "plan_only"
        assert extra["project"] == "x/y"


def test_apply_plan_review_gate_reject_does_not_post_queue(monkeypatch, tmp_path):
    """REJECT_PLAN end-to-end: NO POST to /api/queue, status flips to
    plan_rejected.
    """
    import json
    from agent import plan_review_gate as g

    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps({
        "plan_verdicts": [
            {"verdict": "REJECT_PLAN", "employee_index": 0, "issue_number": 7,
             "feedback": "fundamentally broken approach"},
        ],
    }))

    queue_calls: list = []
    event_calls: list[str] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **kw: queue_calls.append(a) or {"id": 99})
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **kw: event_calls.append(ev) or True)

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=verdicts_file,
        project_repo="x/y",
        run_id="run-2",
    )

    assert len(outcomes) == 1
    assert outcomes[0].action_kind == "reject"
    assert outcomes[0].queue_item_id is None
    # CRITICAL: no queue POST on reject.
    assert queue_calls == []
    # Status flips: awaiting_plan_review (initial) → plan_rejected (terminal).
    assert event_calls == ["awaiting_plan_review", "plan_rejected"]


def test_apply_plan_review_gate_revise_writes_feedback_no_queue_post(monkeypatch, tmp_path):
    """REVISE_PLAN within budget: NO queue POST, feedback is written to
    workspace, run stays in awaiting_plan_review (not flipped to a
    terminal state).
    """
    import json
    from pathlib import Path
    from agent import plan_review_gate as g

    monkeypatch.setenv("STATION_PLAN_REVISION_MAX", "3")
    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps({
        "plan_verdicts": [
            {"verdict": "REVISE_PLAN", "employee_index": 0, "issue_number": 9,
             "plan_path": "/ws/.claude-employee-plan-0.json",
             "feedback": "Add error handling for null inputs"},
        ],
    }))

    workspace = tmp_path / "ws"

    queue_calls: list = []
    event_calls: list[str] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **kw: queue_calls.append(a) or {"id": 99})
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **kw: event_calls.append(ev) or True)

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=verdicts_file,
        project_repo="x/y",
        run_id="run-3",
        workspace=workspace,
        revision_count=0,
    )

    assert len(outcomes) == 1
    assert outcomes[0].action_kind == "revise"
    # No queue POST on revise.
    assert queue_calls == []
    # Run stays in awaiting_plan_review (not plan_approved or plan_rejected).
    assert event_calls == ["awaiting_plan_review", "awaiting_plan_review"]
    # Feedback file exists and contains the manager's text.
    fb_path = outcomes[0].feedback_path
    assert fb_path is not None
    fb_data = json.loads(Path(fb_path).read_text())
    assert fb_data["feedback"] == "Add error handling for null inputs"
    assert fb_data["prior_plan_path"] == "/ws/.claude-employee-plan-0.json"
    assert fb_data["revision_count"] == 1


def test_apply_plan_review_gate_revise_past_budget_rejects(monkeypatch, tmp_path):
    """REVISE_PLAN past STATION_PLAN_REVISION_MAX → halt → terminal
    plan_rejected event, no queue POST.
    """
    import json
    from agent import plan_review_gate as g

    monkeypatch.setenv("STATION_PLAN_REVISION_MAX", "2")
    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps({
        "plan_verdicts": [
            {"verdict": "REVISE_PLAN", "employee_index": 0, "issue_number": 9,
             "plan_path": "/ws/p.json", "feedback": "still no good"},
        ],
    }))

    queue_calls: list = []
    event_calls: list[str] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **kw: queue_calls.append(a) or {"id": 99})
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **kw: event_calls.append(ev) or True)

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=verdicts_file,
        project_repo="x/y",
        run_id="run-4",
        revision_count=2,  # already at the budget
    )

    assert len(outcomes) == 1
    assert outcomes[0].action_kind == "halt_revisions_exhausted"
    assert queue_calls == []
    assert event_calls == ["awaiting_plan_review", "plan_rejected"]


def test_apply_plan_review_gate_queue_post_failure_does_not_flip_to_approved(
    monkeypatch, tmp_path,
):
    """When the queue POST fails, the run must NOT be marked plan_approved
    (that would silently lose work). Stay in awaiting_plan_review.
    """
    import json
    from agent import plan_review_gate as g

    verdicts_file = tmp_path / "verdicts.json"
    verdicts_file.write_text(json.dumps({
        "plan_verdicts": [
            {"verdict": "APPROVE_PLAN", "employee_index": 0, "issue_number": 42,
             "plan_path": "/ws/p.json", "feedback": "ok"},
        ],
    }))

    event_calls: list[str] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **kw: None)  # simulate failure
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **kw: event_calls.append(ev) or True)

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=verdicts_file,
        project_repo="x/y",
        run_id="run-fail",
    )

    assert len(outcomes) == 1
    assert outcomes[0].queue_item_id is None
    assert outcomes[0].action_kind == "enqueue_full_run"
    # Initial awaiting + a final awaiting — never plan_approved.
    assert "plan_approved" not in event_calls


def test_apply_plan_review_gate_missing_verdicts_file_keeps_awaiting(
    monkeypatch, tmp_path,
):
    """Malformed/missing verdicts file: run stays in awaiting_plan_review
    for manual operator resolution; no queue POST.
    """
    from agent import plan_review_gate as g

    queue_calls: list = []
    event_calls: list[str] = []
    monkeypatch.setattr(g, "post_queue_item", lambda *a, **kw: queue_calls.append(a) or {"id": 99})
    monkeypatch.setattr(g, "post_run_event", lambda ev, rid, **kw: event_calls.append(ev) or True)

    outcomes = g.apply_plan_review_gate(
        project_mode="plan_only",
        verdicts_path=tmp_path / "missing.json",
        project_repo="x/y",
        run_id="run-missing",
    )

    assert outcomes == []
    assert queue_calls == []
    # Just the initial awaiting_plan_review event — no terminal flip.
    assert event_calls == ["awaiting_plan_review"]


def test_post_queue_item_uses_dashboard_url_and_bearer_auth(monkeypatch):
    """post_queue_item respects STATION_DASHBOARD_URL and STATION_API_KEY."""
    import httpx
    from agent import plan_review_gate as g

    monkeypatch.setenv("STATION_DASHBOARD_URL", "http://dash:9999")
    monkeypatch.setenv("STATION_API_KEY", "tok-xyz")

    captured = {}

    class _StubClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            class R:
                status_code = 201
                def json(self): return {"id": 11}
            return R()

    monkeypatch.setattr(httpx, "Client", _StubClient)

    out = g.post_queue_item({"project_repo": "x/y", "mode": "full", "state": "pending"})
    assert out == {"id": 11}
    assert captured["url"] == "http://dash:9999/api/queue"
    assert captured["headers"]["Authorization"] == "Bearer tok-xyz"
    assert captured["body"]["mode"] == "full"


def test_post_queue_item_returns_none_on_network_error(monkeypatch):
    """Network failure must be swallowed — gate is best-effort."""
    import httpx
    from agent import plan_review_gate as g

    class _StubClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw):
            raise httpx.RequestError("connection refused")

    monkeypatch.setattr(httpx, "Client", _StubClient)
    assert g.post_queue_item({"project_repo": "x/y"}) is None


def test_post_run_event_uses_webhook_secret_when_configured(monkeypatch):
    """post_run_event sends X-Webhook-Token when STATION_WEBHOOK_SECRET is set."""
    import httpx
    from agent import plan_review_gate as g

    monkeypatch.setenv("STATION_WEBHOOK_SECRET", "wh-tok")
    monkeypatch.delenv("STATION_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("STATION_WEBHOOK_URL", "http://wh:8420/api/webhook/run-event")

    captured = {}

    class _StubClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            class R:
                status_code = 200
                text = ""
            return R()

    monkeypatch.setattr(httpx, "Client", _StubClient)

    ok = g.post_run_event("plan_approved", "run-1", extra={"project": "x/y"})
    assert ok is True
    # URL is the dashboard base + /api/webhook/run-event
    assert captured["url"] == "http://wh:8420/api/webhook/run-event"
    assert captured["headers"]["X-Webhook-Token"] == "wh-tok"
    assert captured["body"]["event"] == "plan_approved"
    assert captured["body"]["run_id"] == "run-1"
    assert captured["body"]["project"] == "x/y"


# --- Webhook-side handlers (issue #266) ------------------------------------


async def test_handle_awaiting_plan_review_sets_status():
    """The webhook lifecycle handler flips Run.status to awaiting_plan_review."""
    from datetime import datetime, timezone
    from app.models import Run
    from app.schemas import WebhookRunEvent
    from app.services import run_lifecycle

    class _StubDb:
        def __init__(self): self.added = []
        def add(self, o): self.added.append(o)

    db = _StubDb()
    event = WebhookRunEvent(event="awaiting_plan_review", run_id="run-1", project="x/y")
    run = Run(run_id="run-1", project_id=1, status="reviewing",
              started_at=datetime.now(timezone.utc))
    out = await run_lifecycle.handle_awaiting_plan_review(db, event, 1, run)
    assert out.status == "awaiting_plan_review"


async def test_handle_plan_approved_sets_terminal():
    from datetime import datetime, timezone
    from app.models import Run
    from app.schemas import WebhookRunEvent
    from app.services import run_lifecycle

    class _StubDb:
        def __init__(self): self.added = []
        def add(self, o): self.added.append(o)

    db = _StubDb()
    event = WebhookRunEvent(event="plan_approved", run_id="run-2", project="x/y")
    run = Run(run_id="run-2", project_id=1, status="awaiting_plan_review",
              started_at=datetime.now(timezone.utc))
    out = await run_lifecycle.handle_plan_approved(db, event, 1, run)
    assert out.status == "plan_approved"
    assert out.finished_at is not None


async def test_handle_plan_rejected_sets_terminal():
    from datetime import datetime, timezone
    from app.models import Run
    from app.schemas import WebhookRunEvent
    from app.services import run_lifecycle

    class _StubDb:
        def __init__(self): self.added = []
        def add(self, o): self.added.append(o)

    db = _StubDb()
    event = WebhookRunEvent(event="plan_rejected", run_id="run-3", project="x/y")
    run = Run(run_id="run-3", project_id=1, status="awaiting_plan_review",
              started_at=datetime.now(timezone.utc))
    out = await run_lifecycle.handle_plan_rejected(db, event, 1, run)
    assert out.status == "plan_rejected"
    assert out.finished_at is not None


def test_webhook_router_registers_plan_review_handlers():
    """Regression guard: the three new run lifecycle events must be wired
    into the webhook router's _RUN_HANDLERS dispatch table.
    """
    from app.routers import webhook
    for ev in ("awaiting_plan_review", "plan_approved", "plan_rejected"):
        assert ev in webhook._RUN_HANDLERS, (
            f"webhook router missing handler for {ev}"
        )



# --- Frontend dropdown values match backend (issue #266) -------------------


def test_frontend_dropdown_values_match_backend_modes():
    """Smoke test: the four <option value="..."> entries in ProjectDetail
    and ProjectsPage must match VALID_PROJECT_MODES on the backend.
    Drift in either direction is a defect.

    Help-text phrases under the dropdown are deliberately not asserted —
    the cyberpunk UI redesign removed them in favour of a different
    affordance, and presence/wording of help copy is a UI decision, not
    a backend contract.
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


# --- Issue #266 follow-up: orchestrator drains pending queue items ---------


@pytest.mark.asyncio
async def test_claim_pending_queue_items_returns_empty_when_none(monkeypatch):
    """No pending items for the project → empty list. Guards against the
    helper accidentally claiming items belonging to other projects."""
    from app.database import Base, async_session, engine
    from app.models import Project, QueueItem, StationControl

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as s:
            s.add(StationControl(id=1, global_pause=False))
            s.add(Project(repo="x/y", priority="medium", branch="main"))
            await s.commit()
        # An item for a DIFFERENT repo — must NOT be returned.
        async with async_session() as s:
            s.add(QueueItem(project_repo="other/repo", issue_number=99,
                            mode="full", state="pending"))
            await s.commit()

        items = await station_orchestrator.claim_pending_queue_items(
            "x/y", "test-run-1",
        )
        assert items == []
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_claim_pending_queue_items_marks_claimed_and_fetches_issue(
    monkeypatch,
):
    """Happy path: pending items are transitioned to ``claimed``, run_id
    is bound, the issue dict shape matches what fetch_eligible_issues
    returns, and approved_plan_path comes through from the context JSON.
    """
    from app.database import Base, async_session, engine
    from app.models import Project, QueueItem, StationControl
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as s:
            s.add(StationControl(id=1, global_pause=False))
            s.add(Project(repo="x/y", priority="medium", branch="main"))
            ctx = '{"approved_plan_path": "/ws/.claude-employee-plan-0.json", "from_plan_only_run": true}'
            s.add(QueueItem(project_repo="x/y", issue_number=42,
                            mode="full", state="pending", context=ctx))
            await s.commit()

        # Stub gh issue view → return a realistic issue payload.
        def fake_run(cmd, *a, **kw):
            assert cmd[:3] == ["gh", "issue", "view"]
            payload = '{"number":42,"title":"T","body":"B","labels":[{"name":"bug"}]}'
            return type("R", (), {"returncode": 0, "stdout": payload, "stderr": ""})()
        monkeypatch.setattr("agent.station_orchestrator.subprocess.run", fake_run)

        items = await station_orchestrator.claim_pending_queue_items(
            "x/y", "test-run-2",
        )
        assert len(items) == 1
        c = items[0]
        assert c.queue_mode == "full"
        assert c.approved_plan_path == "/ws/.claude-employee-plan-0.json"
        assert c.issue["number"] == 42
        assert c.issue["title"] == "T"
        assert c.issue["body"] == "B"
        assert c.issue["labels"] == [{"name": "bug"}]

        async with async_session() as s:
            qi = (await s.execute(
                select(QueueItem).where(QueueItem.id == c.queue_item_id)
            )).scalar_one()
            assert qi.state == "claimed"
            assert qi.run_id == "run-test-run-2"
            assert qi.assigned_at is not None
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_claim_pending_queue_items_marks_failed_on_gh_error(monkeypatch):
    """When ``gh issue view`` errors (closed issue, network fail, etc.),
    the queue item is moved to ``failed`` with the error captured —
    don't return a half-baked dict the orchestrator will choke on."""
    from app.database import Base, async_session, engine
    from app.models import Project, QueueItem, StationControl
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as s:
            s.add(StationControl(id=1, global_pause=False))
            s.add(Project(repo="x/y", priority="medium", branch="main"))
            s.add(QueueItem(project_repo="x/y", issue_number=999,
                            mode="full", state="pending"))
            await s.commit()

        def fake_run(*_a, **_kw):
            return type("R", (), {
                "returncode": 1, "stdout": "",
                "stderr": "GraphQL: Could not resolve to an Issue with the number of 999",
            })()
        monkeypatch.setattr("agent.station_orchestrator.subprocess.run", fake_run)

        items = await station_orchestrator.claim_pending_queue_items(
            "x/y", "test-run-3",
        )
        assert items == []  # nothing returned for the orchestrator to work on

        async with async_session() as s:
            qi = (await s.execute(select(QueueItem))).scalar_one()
            assert qi.state == "failed"
            assert "Could not resolve" in (qi.error_message or "")
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_finalise_claimed_queue_items_marks_completed():
    """Finalisation flips claimed items to the requested terminal
    state. Different runs should never see them as pending again."""
    from app.database import Base, async_session, engine
    from app.models import QueueItem, StationControl
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as s:
            s.add(StationControl(id=1, global_pause=False))
            s.add(QueueItem(project_repo="x/y", issue_number=1,
                            mode="full", state="claimed"))
            s.add(QueueItem(project_repo="x/y", issue_number=2,
                            mode="full", state="claimed"))
            await s.commit()
            row1 = (await s.execute(
                select(QueueItem).where(QueueItem.issue_number == 1)
            )).scalar_one()
            row2 = (await s.execute(
                select(QueueItem).where(QueueItem.issue_number == 2)
            )).scalar_one()

        claimed = [
            station_orchestrator._ClaimedQueueItem(
                queue_item_id=row1.id, queue_mode="full",
                approved_plan_path=None, issue={"number": 1},
            ),
            station_orchestrator._ClaimedQueueItem(
                queue_item_id=row2.id, queue_mode="full",
                approved_plan_path=None, issue={"number": 2},
            ),
        ]
        await station_orchestrator.finalise_claimed_queue_items(
            claimed, outcome="completed",
        )

        async with async_session() as s:
            states = sorted(
                (await s.execute(select(QueueItem.state))).scalars().all()
            )
            assert states == ["completed", "completed"]
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


def test_build_team_prompt_surfaces_approved_plans_section():
    """When approved_plan_paths is provided, the spawn prompt must
    contain an APPROVED_PLAN section the lead can route teammates to."""
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 13, "title": "T", "body": "B", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="full",
        approved_plan_paths=[
            "/ws/.claude-employee-plan-0.json",
            "/ws/.claude-employee-plan-1.json",
        ],
    )
    assert "Approved plans from a prior plan_only run" in prompt
    assert "/ws/.claude-employee-plan-0.json" in prompt
    assert "/ws/.claude-employee-plan-1.json" in prompt


def test_build_team_prompt_omits_approved_plans_section_when_none():
    """No approved_plan_paths → no section. Otherwise the lead would
    talk about plans that don't exist."""
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 13, "title": "T", "body": "B", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="full",
    )
    assert "Approved plans from a prior plan_only run" not in prompt


def test_build_team_prompt_approved_plans_swaps_workflow_to_implementation_only():
    """Regression: run-20260509T183351Z burned ~43 min spawning
    "Wait for plan submissions" teammates because the workflow still said
    "Require plan approval before any teammate starts implementation".
    With approved_plan_paths, the lead must see an implementation-only
    workflow that explicitly tells it not to wait for plan approval."""
    prompt = station_orchestrator.build_team_prompt(
        repo="x/y",
        issues=[{"number": 13, "title": "T", "body": "B", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="full",
        approved_plan_paths=["/ws/.claude-employee-plan-0.json"],
    )
    assert "IMPLEMENTATION — plans pre-approved" in prompt
    assert "Skip plan approval" in prompt
    assert "Require plan approval" not in prompt
    # Approved-plan section must precede the workflow so the lead reads
    # it first (it's now part of the same instruction surface, not a
    # footer the lead may skim past).
    assert prompt.index("Approved plans from a prior plan_only run") < prompt.index(
        "Your Workflow"
    )


def test_build_team_prompt_bans_spawn_as_sleep_proxy():
    """Regression for the same run: the lead used Task spawn as a sleep
    primitive ("Wait 3 min then check progress" teammates). Active
    Monitoring must explicitly forbid that pattern in both branches."""
    base_kwargs = dict(
        repo="x/y",
        issues=[{"number": 13, "title": "T", "body": "B", "labels": []}],
        config={},
        run_id="run-x",
        workspace="/ws",
        worktree_paths={"backend": "/ws-b"},
        project_mode="full",
    )
    for paths in (None, ["/ws/.claude-employee-plan-0.json"]):
        prompt = station_orchestrator.build_team_prompt(
            **base_kwargs, approved_plan_paths=paths
        )
        assert "Do **NOT** spawn a teammate just to wait" in prompt
        assert "sleep proxies" in prompt


# --- Prompt stream lifecycle -------


def test_orchestrator_wiring_no_user_prompt_stream():
    """Per #384, _user_prompt_stream no longer exists."""
    import importlib
    so = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(so, "_user_prompt_stream")


def test_orchestrator_wiring_no_force_exit_with_cleanup():
    """Per #384, _force_exit_with_cleanup no longer exists."""
    import importlib
    so = importlib.import_module("agent.station_orchestrator")
    assert not hasattr(so, "_force_exit_with_cleanup")


def test_launcher_does_not_set_stream_close_timeout_in_run_env():
    """Negative regression: ``agent.launcher.trigger()`` MUST NOT set
    ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT`` in the subprocess env.

    After issue #392 the launcher stops being the policy owner. Modules
    that still depend on the bundled CLI's hook-callback lifetime own
    their own setter (see ``agent.conflict_resolver.sdk_runner``).

    This is a source-level test — we don't boot the launcher.
    """
    import inspect
    from agent import launcher

    src = inspect.getsource(launcher)
    assert 'CLAUDE_CODE_STREAM_CLOSE_TIMEOUT' not in src, (
        "agent.launcher must not set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT "
        "(issue #392). Move the setter into the module that owns the "
        "surviving query() call."
    )


@pytest.mark.asyncio
async def test_control_poll_loop_emits_periodic_heartbeats(tmp_path, monkeypatch):
    """The control poll loop MUST emit a ``heartbeat`` webhook on its
    own cadence so the dashboard's stale-run reaper sees liveness
    during long quiet windows. Without this, the reaper marks any
    ``running`` row ``interrupted`` after 120s of webhook silence —
    a window an Agent Teams Sonnet manager-review turn easily exceeds.

    Regression guard for run-20260516T005654Z: the run flipped to
    ``interrupted`` at t=605s while the container was healthy and
    still emitting launcher ticks. The dashboard reaper had a
    different signal (``last_event_at``) that no one was feeding.
    """
    import asyncio
    import sqlite3

    from agent import station_orchestrator

    # Minimal sqlite for the control drain.
    db = tmp_path / "hb.db"
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

    # Spy on post_webhook so we can count heartbeats.
    captured: list[tuple[str, dict]] = []

    def _spy(config, event, data=None):
        captured.append((event, data or {}))

    monkeypatch.setattr(station_orchestrator, "post_webhook", _spy)
    # Shrink the heartbeat interval so the test runs in fractions of a
    # second. The default is 30s; we want ~3 heartbeats in 0.4s.
    monkeypatch.setattr(station_orchestrator, "HEARTBEAT_INTERVAL_SECONDS", 0.1)

    flags = {"stop": False}
    task = asyncio.create_task(
        station_orchestrator._control_poll_loop(
            "run-hb-test", {}, [], flags, interval=0.02,
        )
    )

    try:
        await asyncio.sleep(0.4)
    finally:
        flags["stop"] = True
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()

    heartbeats = [d for (ev, d) in captured if ev == "heartbeat"]
    assert len(heartbeats) >= 3, (
        f"expected ≥3 heartbeats in 0.4s with interval=0.1s; "
        f"got {len(heartbeats)}: {captured}"
    )
    for d in heartbeats:
        assert d.get("run_id") == "run-hb-test", (
            f"heartbeat payload must carry the full_run_id; got {d}"
        )


@pytest.mark.asyncio
async def test_control_poll_loop_heartbeat_is_best_effort(tmp_path, monkeypatch):
    """A failing post_webhook (dashboard down, network error) MUST NOT
    crash the control poll loop. Heartbeats are diagnostic, not
    load-bearing — losing one is acceptable; killing the loop is not.
    """
    import asyncio
    import sqlite3

    from agent import station_orchestrator

    db = tmp_path / "hb-fail.db"
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

    call_count = {"n": 0}

    def _boom(config, event, data=None):
        call_count["n"] += 1
        raise RuntimeError("simulated dashboard outage")

    monkeypatch.setattr(station_orchestrator, "post_webhook", _boom)
    monkeypatch.setattr(station_orchestrator, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    flags = {"stop": False}
    task = asyncio.create_task(
        station_orchestrator._control_poll_loop(
            "run-hb-fail", {}, [], flags, interval=0.02,
        )
    )

    try:
        await asyncio.sleep(0.25)
        # Loop must still be running despite repeated post_webhook crashes.
        assert not task.done(), (
            f"control poll loop crashed on heartbeat failure; "
            f"exception: {task.exception() if task.done() else None}"
        )
        assert call_count["n"] >= 2, (
            f"heartbeat must be attempted repeatedly; got {call_count['n']}"
        )
    finally:
        flags["stop"] = True
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
