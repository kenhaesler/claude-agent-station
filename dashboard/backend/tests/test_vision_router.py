import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.database import Base, async_session, engine, init_db
from app.models import Project
from app.services.github_contents import ContentsResult, FileNotFound


@pytest_asyncio.fixture
async def project():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        p = Project(repo="o/r", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        yield p
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_get_vision_serves_cache_when_fresh(project):
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "abc"
        proj.vision_cached_body = "# cached"
        proj.vision_cached_at = datetime.now(timezone.utc)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file", new=AsyncMock()) as m:
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 200
    assert r.json()["sha"] == "abc"
    assert r.json()["body"] == "# cached"
    m.assert_not_called()  # cache was used


@pytest.mark.asyncio
async def test_get_vision_falls_through_to_github_when_stale(project):
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await db.commit()

    fake = ContentsResult(sha="new-sha", body="# fresh", html_url="x")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file", new=AsyncMock(return_value=fake)):
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 200
    assert r.json()["sha"] == "new-sha"


@pytest.mark.asyncio
async def test_get_vision_returns_404_when_file_absent(project):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file",
                   new=AsyncMock(side_effect=FileNotFound("o/r:main:docs/vision.md"))):
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_vision_renders_and_commits(project):
    fake_sha = "new-blob-sha"
    fake_html = "https://github.com/o/r/blob/main/docs/vision.md"
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "old-sha"
        await db.commit()

    body = {
        "vision_doc": {
            "problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
            "principles": "Pr", "horizons": "H", "anti_patterns": "A",
        }
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value=fake_sha)) as m, \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha=fake_sha, body="# Vision — o/r\n...", html_url=fake_html))):
            r = await c.post(f"/api/projects/{project.id}/vision", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["sha"] == fake_sha
    assert r.json()["html_url"] == fake_html

    # Verify the rendered body was sent
    args, kwargs = m.call_args
    assert "## Problem" in kwargs["body"]
    assert "## Anti-patterns" in kwargs["body"]
    assert kwargs["current_sha"] == "old-sha"


@pytest.mark.asyncio
async def test_post_vision_409_on_stale_sha_returns_envelope(project):
    from app.services.github_contents import StaleSha
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "stale-sha"
        await db.commit()

    body = {"vision_doc": {k: "x" for k in [
        "problem", "users", "end_state", "non_goals",
        "principles", "horizons", "anti_patterns",
    ]}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file",
                   new=AsyncMock(side_effect=StaleSha(current_sha="newer-sha", current_body="# external"))):
            r = await c.post(f"/api/projects/{project.id}/vision", json=body)
    assert r.status_code == 409
    payload = r.json()["detail"]
    assert payload["code"] == "stale_sha"
    assert payload["current_sha"] == "newer-sha"
    assert payload["current_body"] == "# external"


@pytest.mark.asyncio
async def test_post_vision_chat_streams_sse_events(project):
    """SSE endpoint yields events for assistant text, coverage, done."""
    async def fake_run_chat_turn(db, *, session_id, user_message, system_prompt, model, sdk_session_id):
        yield {"type": "assistant_text", "delta": "hi"}
        yield {"type": "coverage_update", "covered": ["problem"], "remaining": []}
        yield {"type": "done"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.vision_chat.run_chat_turn", new=fake_run_chat_turn):
            async with c.stream(
                "POST",
                f"/api/projects/{project.id}/vision/chat",
                json={"session_id": None, "message": "hi"},
            ) as r:
                assert r.status_code == 200
                lines = []
                async for line in r.aiter_lines():
                    lines.append(line)
                    if len(lines) > 12: break
    text = "\n".join(lines)
    assert "event: assistant_text" in text
    assert "event: coverage_update" in text
    assert "event: done" in text


@pytest.mark.asyncio
async def test_post_vision_chat_409_when_session_already_active(project):
    from app.services import vision_chat as vc_service
    async with async_session() as db:
        await vc_service.create_session(db, project.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            f"/api/projects/{project.id}/vision/chat",
            json={"session_id": None, "message": "hi"},
        )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "session_exists"
    assert detail["session_id"]


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_404_when_none(project):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_session(project):
    from app.services import vision_chat as vc_service
    async with async_session() as db:
        await vc_service.create_session(db, project.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 200
    assert r.json()["state"] == "active"
    assert r.json()["phase"] == "freeform"
    assert r.json()["coverage"] == {}


@pytest.mark.asyncio
async def test_delete_chat_session_marks_cancelled(project):
    from app.services import vision_chat as vc_service
    async with async_session() as db:
        s = await vc_service.create_session(db, project.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.delete(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 204
    async with async_session() as db:
        from app.models import VisionChatSession
        refreshed = await db.get(VisionChatSession, s.id)
        assert refreshed.state == "cancelled"


@pytest.mark.asyncio
async def test_find_gaps_calls_service_control(project):
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_body = "# Vision — o/r\n\n## Problem\nP\n"
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": True, "status_code": 200, "pid": 99})):
            r = await c.post(f"/api/projects/{project.id}/vision/find-gaps")
    assert r.status_code == 200
    assert r.json()["status"] == "triggered"


# ---------------------------------------------------------------------------
# Trigger B — auto-fire analyst on vision commit
# ---------------------------------------------------------------------------

def _commit_body():
    return {
        "vision_doc": {
            "problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
            "principles": "Pr", "horizons": "H", "anti_patterns": "A",
        }
    }


@pytest.mark.asyncio
async def test_commit_vision_fires_analyst_when_sha_changes(project):
    """Two commits with different fresh.sha values → start_vision_analyst called twice."""
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "old-sha"
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        analyst_mock = AsyncMock(return_value={"success": True, "status_code": 200, "pid": 1})
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value="sha-1")), \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha="sha-1", body="# v1", html_url="http://x"))), \
             patch("app.services.service_control.start_vision_analyst", new=analyst_mock):
            r1 = await c.post(f"/api/projects/{project.id}/vision", json=_commit_body())
        assert r1.status_code == 200

        # Second commit with a different SHA
        analyst_mock2 = AsyncMock(return_value={"success": True, "status_code": 200, "pid": 2})
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value="sha-2")), \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha="sha-2", body="# v2", html_url="http://x"))), \
             patch("app.services.service_control.start_vision_analyst", new=analyst_mock2):
            r2 = await c.post(f"/api/projects/{project.id}/vision", json=_commit_body())
        assert r2.status_code == 200

    analyst_mock.assert_called_once()
    analyst_mock2.assert_called_once()


@pytest.mark.asyncio
async def test_commit_vision_skips_analyst_when_sha_unchanged(project):
    """Second commit returns the same fresh.sha → analyst called only once total."""
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "old-sha"
        await db.commit()

    transport = ASGITransport(app=app)
    analyst_mock = AsyncMock(return_value={"success": True, "status_code": 200, "pid": 1})
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # First commit — new SHA → analyst fires
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value="same-sha")), \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha="same-sha", body="# v1", html_url="http://x"))), \
             patch("app.services.service_control.start_vision_analyst", new=analyst_mock):
            r1 = await c.post(f"/api/projects/{project.id}/vision", json=_commit_body())
        assert r1.status_code == 200

        # Second commit — same SHA returned → analyst must NOT fire again
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value="same-sha")), \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha="same-sha", body="# v1", html_url="http://x"))), \
             patch("app.services.service_control.start_vision_analyst", new=analyst_mock):
            r2 = await c.post(f"/api/projects/{project.id}/vision", json=_commit_body())
        assert r2.status_code == 200

    analyst_mock.assert_called_once()


@pytest.mark.asyncio
async def test_commit_vision_treats_409_as_success(project):
    """409 from launcher is treated as success: commit returns 200 and SHA is advanced."""
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "old-sha"
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value="new-sha")), \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha="new-sha", body="# v", html_url="http://x"))), \
             patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": False, "status_code": 409, "error": "already running"})):
            r = await c.post(f"/api/projects/{project.id}/vision", json=_commit_body())
    assert r.status_code == 200

    # SHA must have been advanced so identical re-commits don't refire
    async with async_session() as db:
        proj = await db.get(Project, project.id)
        assert proj.last_vision_analyzed_sha == "new-sha"


@pytest.mark.asyncio
async def test_vision_proposals_endpoint_returns_counts(project, monkeypatch):
    """GET /api/projects/{id}/vision/proposals returns open + accepted_recent."""
    import subprocess
    from app.routers import vision as vision_router

    # Clear the module-level cache so we don't get a stale hit
    vision_router._PROPOSALS_CACHE.clear()

    open_payload = '[{"number": 1}, {"number": 2}, {"number": 3}]'
    closed_payload = '[{"number": 99}]'

    def fake_run(cmd, *a, **k):
        # Distinguish open vs closed by inspecting the --state flag
        try:
            state = cmd[cmd.index("--state") + 1]
        except (ValueError, IndexError):
            state = ""
        if state == "open":
            return type("R", (), {"returncode": 0, "stdout": open_payload, "stderr": ""})()
        if state == "closed":
            return type("R", (), {"returncode": 0, "stdout": closed_payload, "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/projects/{project.id}/vision/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open"] == 3
    assert body["accepted_recent"] == 1
