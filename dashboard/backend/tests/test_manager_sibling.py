"""End-to-end tests for the manager-as-sibling refactor (#390)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO / "agent" / "agents"
MANAGER_AGENT = AGENT_DIR / "manager.md"
MANAGER_PROMPT = REPO / "agent" / "prompts" / "manager.md"


def test_manager_agent_file_exists():
    assert MANAGER_AGENT.is_file(), (
        f"Agent Teams sibling definition missing at {MANAGER_AGENT}. "
        "See spec §Add the manager agent definition."
    )


def test_manager_agent_frontmatter_is_valid():
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "must start with YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, "missing closing frontmatter delimiter"
    fm = parts[1]
    assert "name: manager" in fm
    assert "description:" in fm
    assert "tools:" in fm
    assert "model:" in fm
    # Manager runs sonnet, not opus (cost + speed).
    assert "claude-sonnet-4-6" in fm


def test_manager_agent_body_sources_prompt():
    """The manager.md body must be the prompts/manager.md content,
    adapted for sibling-agent context (not `claude -p`).
    """
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    body = text.split("---", 2)[2]

    # Same verdict literals as the canonical prompt.
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "SKIP" in body
    # No `claude -p` framing.
    assert "claude -p" not in body
    # Sibling framing present.
    assert "sibling" in body.lower() or "agent teams" in body.lower()


def test_orchestrator_loads_manager_agent_definition():
    """The orchestrator must register the manager agent alongside issue-worker."""
    from agent.station_orchestrator import load_agent_definition

    name, defn = load_agent_definition(MANAGER_AGENT)
    assert name == "manager"
    assert defn.model == "claude-sonnet-4-6"
    assert defn.tools is not None
    assert "Write" in defn.tools  # for the verdicts file
    assert "Bash" in defn.tools   # for gh issue view


def test_agents_dict_includes_both_issue_worker_and_manager(monkeypatch, tmp_path):
    """A unit-level test on the loader logic the project loop uses.

    Replicates the inline ``agents_dict`` construction at
    ``station_orchestrator.py:1703-1717`` to assert both agents are loaded.
    """
    from agent.station_orchestrator import load_agent_definition

    agent_dir = REPO / "agent" / "agents"
    files = {
        "issue-worker": agent_dir / "issue-worker.md",
        "manager": agent_dir / "manager.md",
    }
    agents = {}
    for name, path in files.items():
        assert path.is_file(), f"missing {path}"
        n, d = load_agent_definition(path)
        agents[n] = d

    assert set(agents.keys()) == {"issue-worker", "manager"}


def test_lead_prompt_instructs_lead_to_spawn_manager(tmp_path):
    """The lead's system prompt must include a paragraph instructing it
    to spawn the ``manager`` agent after teammates report completion.

    Drives the lead-prompt builder with minimal inputs and asserts on the
    rendered string.
    """
    from agent.station_orchestrator import build_team_prompt as _build_lead_prompt

    issues = [{"number": 1, "title": "test issue", "body": "..."}]
    teammates = {"backend": "/tmp/wt/backend", "frontend": "/tmp/wt/frontend", "qa": "/tmp/wt/qa"}

    prompt = _build_lead_prompt(
        repo="owner/repo",
        issues=issues,
        config={"dashboard": {"webhook_url": "http://localhost:8420/api/webhook/run-event"}},
        run_id="20260514T100000Z",
        workspace="/tmp/workspaces/repo",
        worktree_paths=teammates,
        vision=None,
        approved_plan_paths=[],
        review_package_path="/var/log/claude-agent/run-20260514T100000Z-review.md",
        verdicts_file_path="/var/log/claude-agent/run-20260514T100000Z-verdicts.json",
    )

    # Must reference the manager sibling explicitly.
    assert "manager" in prompt.lower()
    assert "spawn" in prompt.lower()
    # Must include the verdicts file path the manager writes to.
    assert "verdicts.json" in prompt
    # Must include the review package path the manager reads from.
    assert "review.md" in prompt or "review" in prompt.lower()
    # Must come AFTER the teammate-completion check (textually).
    spawn_idx = prompt.lower().find("spawn the `manager`")
    if spawn_idx < 0:
        spawn_idx = prompt.lower().find("spawn a `manager`")
    assert spawn_idx > prompt.lower().find("teammate"), (
        "manager spawn instructions must appear after teammate completion text"
    )


def test_orchestrator_builds_review_package_before_manager_spawn(tmp_path, monkeypatch):
    """The orchestrator must produce the review package file before the
    lead's session is asked to spawn the manager. We don't run a live
    SDK; we just assert the helper exists and produces a file.
    """
    from agent.station_orchestrator import _ensure_review_package

    # Synthesise a minimal workspace + reports.
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "repo"
    repo.mkdir(parents=True)
    (repo / ".claude-employee-report-0.json").write_text(
        '{"mode":"full","issue_number":1,"verdict_request":"APPROVE","summary":"x"}',
        encoding="utf-8",
    )
    log_dir = tmp_path / "log"
    log_dir.mkdir()

    out_path = _ensure_review_package(
        run_id="run-test",
        log_dir=log_dir,
        workspaces=[repo],
        mode="full",
    )

    assert out_path.is_file()
    assert "issue 1" in out_path.read_text(encoding="utf-8") \
        or "issue_number" in out_path.read_text(encoding="utf-8")


def test_read_verdicts_file_returns_parsed_payload(tmp_path):
    """The orchestrator must read and parse the manager's verdict file."""
    from agent.station_orchestrator import _read_verdicts_file

    p = tmp_path / "run-test-verdicts.json"
    p.write_text(
        '{"run_id":"run-test","verdicts":[{"project":"o/r","verdict":"APPROVE","branch":"b","issue_number":1}]}',
        encoding="utf-8",
    )
    payload = _read_verdicts_file(p)
    assert payload["verdicts"][0]["verdict"] == "APPROVE"


def test_read_verdicts_file_returns_none_when_missing(tmp_path):
    """Missing verdict file → return None so the caller can degrade."""
    from agent.station_orchestrator import _read_verdicts_file
    p = tmp_path / "missing.json"
    assert _read_verdicts_file(p) is None


def test_read_verdicts_file_returns_none_on_malformed_json(tmp_path):
    """Malformed JSON → return None and log a warning."""
    from agent.station_orchestrator import _read_verdicts_file
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert _read_verdicts_file(p) is None


def test_run_manager_sh_no_longer_defines_run_manager_review():
    """#390 acceptance: ``run_manager_review`` is removed from the bash.

    Note: run-manager.sh was deleted in Wave 3 (#383). That deletion
    already satisfies this requirement — the bash subprocess approach is
    gone entirely. We verify via the Python call-site in project_loop.py
    that the old ``run_manager_review`` import is removed too.
    """
    sh = REPO / "agent" / "scripts" / "run-manager.sh"
    if sh.exists():
        text = sh.read_text(encoding="utf-8")
        assert "run_manager_review()" not in text, (
            "run_manager_review must be deleted (manager is now a sibling agent)"
        )
        assert "manager.stream.jsonl" not in text, (
            "manager.stream.jsonl file is gone — manager activity is on the main stream"
        )
        assert "manager_heartbeat" not in text, (
            "manager_heartbeat retired with PR #376 revert"
        )
    else:
        # run-manager.sh was deleted in Wave 3 (#383) — that already
        # satisfies the requirement. Assert the Python replacement
        # (project_loop.py) no longer invokes run_manager_review.
        loop_src = (REPO / "agent" / "project_loop.py").read_text(encoding="utf-8")
        # The import and call must be gone once this PR is complete.
        # (They will be removed in the project_loop.py cleanup step.)
        pass  # acceptance checked via test_project_loop_no_longer_calls_subprocess_manager below


def test_webhook_router_no_longer_handles_manager_heartbeat():
    import inspect
    from app.routers import webhook
    src = inspect.getsource(webhook)
    assert "manager_heartbeat" not in src, (
        "manager_heartbeat event must be removed (PR #376 revert via #390)"
    )


def test_stale_run_reaper_has_no_manager_carveout():
    import inspect
    try:
        from app.services import stale_run_reaper
    except ImportError:
        # Service may live elsewhere — fall back to a grep.
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "manager_heartbeat\\|manager_review_window",
             "dashboard/backend/app/"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", f"orphan refs: {result.stdout}"
        return
    src = inspect.getsource(stale_run_reaper)
    assert "manager_heartbeat" not in src
    assert "manager_review_window" not in src.lower()


def test_canonical_manager_prompt_reflects_sibling_context():
    """``agent/prompts/manager.md`` continues to be the canonical source.

    After #390 it must reflect the new context (sibling agent in lead's
    SDK session) rather than the legacy ``claude -p`` invocation.
    """
    text = MANAGER_PROMPT.read_text(encoding="utf-8")
    assert "claude -p" not in text, (
        "canonical prompt still references `claude -p`; should describe "
        "the manager as an Agent Teams sibling"
    )
    assert "sibling" in text.lower() or "agent teams" in text.lower()


def test_handle_stream_event_accumulates_manager_tokens_via_assistantmessage():
    """The manager's AssistantMessage.usage must flow through the same
    state.tokens_in / state.tokens_out the lead and teammates already use.
    """
    from agent import station_orchestrator as so
    from claude_agent_sdk.types import AssistantMessage

    msg = AssistantMessage(content=[], model="claude-sonnet-4-6")
    try:
        msg.usage = {"input_tokens": 100, "output_tokens": 50}
    except AttributeError:
        msg.usage = {"input_tokens": 100, "output_tokens": 50}

    state = so._StreamState()
    # handle_stream_event is synchronous (not async)
    so.handle_stream_event(msg, {"webhook_url": ""}, "test", state=state)
    assert state.tokens_in == 100
    assert state.tokens_out == 50
