import json
import pytest
from unittest.mock import patch, MagicMock
from agent.vision_analyst import (
    propose_gaps,
    format_proposal_body,
    _ensure_workspace,
    VisionAnalystError,
)


VISION = {"problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
          "principles": "Pr", "horizons": "H", "anti_patterns": "A"}


def test_propose_gaps_returns_parsed_proposals():
    fake = json.dumps([
        {"title": "Add daily digest", "body": "Send a daily summary email", "labels": ["feature"], "priority": "medium"},
        {"title": "Cron resilience", "body": "Retry failed cron runs", "labels": ["enhancement"], "priority": "high"},
    ])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=fake):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) == 2
    assert proposals[0]["title"] == "Add daily digest"


def test_propose_gaps_caps_at_5():
    huge = json.dumps([{"title": f"x{i}", "body": "", "labels": [], "priority": "low"} for i in range(20)])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=huge):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) <= 5


def test_propose_gaps_raises_when_model_call_fails():
    """A failed CLI invocation must raise VisionAnalystError so the caller
    can report a run failure rather than silently degrading to 'no gaps'."""
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", side_effect=RuntimeError("boom")):
            with pytest.raises(VisionAnalystError, match="model call failed"):
                propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")


def test_propose_gaps_raises_on_empty_response():
    """An empty stdout from the CLI is a runtime failure — not 'no gaps'."""
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=""):
            with pytest.raises(VisionAnalystError, match="empty response"):
                propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")


def test_propose_gaps_raises_on_non_json_response():
    """Garbage output from the CLI must surface as an error, not [] proposals."""
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value="not json at all"):
            with pytest.raises(VisionAnalystError, match="not JSON"):
                propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")


def test_propose_gaps_returns_empty_list_when_model_says_no_gaps():
    """A legitimate empty list from the model is NOT an error — caller
    should report success-with-zero-proposals."""
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value="[]"):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert proposals == []


def test_format_proposal_body_includes_disclaimer():
    body = format_proposal_body("The feature explanation.")
    assert "Proposed by Claude Station" in body
    assert "vision-suggested" in body
    assert "The feature explanation." in body


async def test_run_for_project_posts_started_and_finished_webhooks(monkeypatch, tmp_path):
    """run_for_project must POST started + finished events with mode=vision-bootstrap."""
    from agent import vision_analyst as va

    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append({"url": url, "json": json})
        class R:
            status_code = 200
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(va.httpx, "post", fake_post, raising=False)
    monkeypatch.setattr(va, "_ensure_workspace", lambda w, r, b="main": True)
    monkeypatch.setattr(va, "load_vision", lambda w: {
        "problem": "p", "users": "u", "end_state": "e",
        "non_goals": "n", "principles": "pr", "horizons": "h",
        "anti_patterns": "a",
    })
    monkeypatch.setattr(va, "propose_gaps", lambda w, v, r, m: [
        {"title": "T1", "body": "B1", "priority": "low"},
    ])
    monkeypatch.setattr(va, "create_proposed_issues", lambda r, p: [(101, p[0])])
    monkeypatch.setenv("STATION_WEBHOOK_URL", "http://test/api/webhook/run-event")
    monkeypatch.setenv("STATION_WORKSPACES", str(tmp_path))

    # Project with id=1
    from app.database import async_session, init_db
    from app.models import Project
    await init_db()
    async with async_session() as db:
        db.add(Project(id=1, repo="x/y", branch="main"))
        await db.commit()

    result = await va.run_for_project(1)
    assert result["ok"] is True

    assert len(posted) == 2
    started = posted[0]["json"]
    finished = posted[1]["json"]
    assert started["event"] == "started"
    assert started["mode"] == "vision-bootstrap"
    assert started["run_id"].startswith("run-vb-")
    assert finished["event"] == "finished"
    assert finished["mode"] == "vision-bootstrap"
    assert finished["status"] == "success"
    assert finished["vision_bootstrap_count"] == 1
    assert finished["vision_bootstrap_proposals"][0]["number"] == 101


def _ok(stdout: str = "") -> object:
    return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


def _fail(stderr: str = "boom", code: int = 1) -> object:
    return type("R", (), {"returncode": code, "stdout": "", "stderr": stderr})()


def test_ensure_workspace_refreshes_existing_clone_with_branch(monkeypatch, tmp_path):
    """When .git already exists, _ensure_workspace must run
    `git fetch --depth 1 origin <branch>` followed by
    `git reset --hard origin/<branch>` against the right branch."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()  # simulate existing clone

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return _ok()

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr("agent.vision_analyst._fetch_gh_token", lambda: None)

    ok = _ensure_workspace(str(workspace), "owner/repo", "develop")
    assert ok is True

    # Two calls: fetch then reset --hard. No clone (git dir already present),
    # no remote-URL rotation (no token available).
    assert len(calls) == 2
    fetch_cmd = calls[0]
    reset_cmd = calls[1]
    assert fetch_cmd == [
        "git", "-C", str(workspace), "fetch", "--depth", "1",
        "origin", "develop",
    ]
    assert reset_cmd == [
        "git", "-C", str(workspace), "reset", "--hard", "origin/develop",
    ]


def test_ensure_workspace_defaults_to_main(monkeypatch, tmp_path):
    """Branch default is 'main' when not supplied."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return _ok()

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr("agent.vision_analyst._fetch_gh_token", lambda: None)
    ok = _ensure_workspace(str(workspace), "owner/repo")
    assert ok is True
    assert calls[0][-1] == "main"
    assert calls[1][-1] == "origin/main"


def test_ensure_workspace_returns_true_when_fetch_fails(monkeypatch, tmp_path, caplog):
    """If `git fetch` fails the function still returns True so the caller
    can fall through to load_vision; the failure is logged."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    def fake_run(cmd, *a, **kw):
        # Fail the first (fetch) call.
        return _fail("network down")

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr("agent.vision_analyst._fetch_gh_token", lambda: None)
    with caplog.at_level("WARNING"):
        ok = _ensure_workspace(str(workspace), "owner/repo", "main")
    assert ok is True
    assert any("git fetch" in rec.message and "failed" in rec.message
               for rec in caplog.records)


def test_ensure_workspace_clones_when_missing_then_refreshes(monkeypatch, tmp_path):
    """When .git is absent, clone first, then run fetch + reset."""
    workspace = tmp_path / "repo"
    # workspace dir does NOT exist yet — clone path

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return _ok()

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr("agent.vision_analyst._fetch_gh_token", lambda: None)
    ok = _ensure_workspace(str(workspace), "owner/repo", "main")
    assert ok is True
    # clone, fetch, reset
    assert len(calls) == 3
    assert calls[0][:3] == ["gh", "repo", "clone"]
    assert calls[1][:5] == ["git", "-C", str(workspace), "fetch", "--depth"]
    assert calls[2][:5] == ["git", "-C", str(workspace), "reset", "--hard"]


def test_ensure_workspace_rotates_remote_url_when_token_available(monkeypatch, tmp_path):
    """With a fresh installation token, _ensure_workspace must rotate the
    workspace's origin URL so subsequent ``git`` ops authenticate. Without
    this, a stale ``ghs_...`` token in the URL fails 401 after ~1 hour."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    calls: list[list[str]] = []
    captured_envs: list[dict | None] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        captured_envs.append(kw.get("env"))
        return _ok()

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent.vision_analyst._fetch_gh_token", lambda: "ghs_FRESHTOKEN",
    )

    ok = _ensure_workspace(str(workspace), "owner/repo", "main")
    assert ok is True

    # remote-set-url, fetch, reset
    assert len(calls) == 3
    assert calls[0][:5] == ["git", "-C", str(workspace), "remote", "set-url"]
    assert calls[0][-1] == "https://x-access-token:ghs_FRESHTOKEN@github.com/owner/repo.git"
    assert calls[1][:5] == ["git", "-C", str(workspace), "fetch", "--depth"]
    assert calls[2][:5] == ["git", "-C", str(workspace), "reset", "--hard"]


def test_ensure_workspace_passes_token_to_clone_env(monkeypatch, tmp_path):
    """When cloning fresh, GH_TOKEN must be set in the subprocess env so
    ``gh repo clone`` can authenticate against private repos."""
    workspace = tmp_path / "repo"
    # No .git — exercises the clone path.

    calls: list[list[str]] = []
    captured_envs: list[dict | None] = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        captured_envs.append(kw.get("env"))
        return _ok()

    monkeypatch.setattr("agent.vision_analyst.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent.vision_analyst._fetch_gh_token", lambda: "ghs_CLONEKEY",
    )

    ok = _ensure_workspace(str(workspace), "owner/repo", "main")
    assert ok is True

    # The clone is the first call. Its env must carry GH_TOKEN.
    clone_env = captured_envs[0]
    assert clone_env is not None, "clone subprocess should have env override"
    assert clone_env.get("GH_TOKEN") == "ghs_CLONEKEY"


def test_fetch_gh_token_returns_token_on_success(monkeypatch):
    """The token endpoint returns ``{"token": "...", "source": "app"}`` on
    success. _fetch_gh_token must return the token string."""
    from agent import vision_analyst as va

    class _Resp:
        status_code = 200

        def json(self):
            return {"token": "ghs_FROMSERVER", "source": "app"}

    class _Client:
        def __init__(self, *_a, **_kw): ...
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def get(self, _url, headers=None):
            return _Resp()

    monkeypatch.setattr(va.httpx, "Client", _Client)
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "x")
    assert va._fetch_gh_token() == "ghs_FROMSERVER"


def test_fetch_gh_token_returns_none_on_404(monkeypatch, caplog):
    """When the dashboard has no GitHub auth configured the endpoint
    returns 404; _fetch_gh_token must return None and log a warning."""
    from agent import vision_analyst as va

    class _Resp:
        status_code = 404
        text = '{"detail":"No GitHub auth configured"}'

    class _Client:
        def __init__(self, *_a, **_kw): ...
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def get(self, _url, headers=None):
            return _Resp()

    monkeypatch.setattr(va.httpx, "Client", _Client)
    with caplog.at_level("WARNING"):
        assert va._fetch_gh_token() is None
    assert any("HTTP 404" in rec.message for rec in caplog.records)


def test_fetch_gh_token_returns_none_on_network_error(monkeypatch, caplog):
    """RequestError (e.g. dashboard unreachable) must be swallowed —
    callers fall back to whatever credential was previously baked into
    the workspace."""
    from agent import vision_analyst as va

    class _Client:
        def __init__(self, *_a, **_kw): ...
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def get(self, _url, headers=None):
            raise va.httpx.ConnectError("dashboard unreachable")

    monkeypatch.setattr(va.httpx, "Client", _Client)
    with caplog.at_level("WARNING"):
        assert va._fetch_gh_token() is None
    assert any("unreachable" in rec.message for rec in caplog.records)


def test_fetch_gh_token_passes_launcher_header(monkeypatch):
    """Endpoint is launcher-token-gated; _fetch_gh_token must pass the
    X-Launcher-Token header from STATION_LAUNCHER_TOKEN."""
    from agent import vision_analyst as va

    captured_headers: list[dict] = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"token": "t"}

    class _Client:
        def __init__(self, *_a, **_kw): ...
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def get(self, _url, headers=None):
            captured_headers.append(headers or {})
            return _Resp()

    monkeypatch.setattr(va.httpx, "Client", _Client)
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-shared-value")
    va._fetch_gh_token()
    assert captured_headers[0].get("X-Launcher-Token") == "secret-shared-value"


def test_create_proposed_issues_pairs_failures_correctly(monkeypatch):
    """When proposal A fails and B succeeds, the (number, proposal) tuple for B
    correctly carries B's title — not A's."""
    from agent import vision_analyst as va

    proposals = [
        {"title": "Proposal A (will fail)", "body": "x", "priority": "low"},
        {"title": "Proposal B (will succeed)", "body": "y", "priority": "low"},
        {"title": "Proposal C (will succeed)", "body": "z", "priority": "low"},
    ]

    call_count = [0]

    def fake_run(cmd, *a, **kw):
        call_count[0] += 1
        # First call (Proposal A) fails
        if call_count[0] == 1:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "gh boom"})()
        # Subsequent calls succeed; URL ends with the issue number
        n = 100 + call_count[0]
        return type("R", (), {
            "returncode": 0,
            "stdout": f"https://github.com/x/y/issues/{n}\n",
            "stderr": "",
        })()

    monkeypatch.setattr("subprocess.run", fake_run)

    pairs = va.create_proposed_issues("x/y", proposals)
    assert len(pairs) == 2
    # First pair: issue 102 should be Proposal B (NOT A)
    assert pairs[0][0] == 102
    assert pairs[0][1]["title"] == "Proposal B (will succeed)"
    # Second pair: issue 103 should be Proposal C
    assert pairs[1][0] == 103
    assert pairs[1][1]["title"] == "Proposal C (will succeed)"
