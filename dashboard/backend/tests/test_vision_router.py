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
