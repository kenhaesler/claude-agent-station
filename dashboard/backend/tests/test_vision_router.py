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
