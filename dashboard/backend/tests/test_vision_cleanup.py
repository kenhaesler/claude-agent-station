import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from app.database import Base, async_session, engine, init_db
from app.models import Project, VisionChatSession
from app.services.vision_cleanup import sweep_stale_sessions


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def project(setup_db):
    async with async_session() as db:
        p = Project(repo="o/r", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p


@pytest.mark.asyncio
async def test_sweep_cancels_active_older_than_24h(project):
    async with async_session() as db:
        s = VisionChatSession(
            id="old-active", project_id=project.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=25),
        )
        db.add(s); await db.commit()
    async with async_session() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert cancelled == 1
    async with async_session() as db:
        refreshed = await db.get(VisionChatSession, "old-active")
        assert refreshed.state == "cancelled"


@pytest.mark.asyncio
async def test_sweep_deletes_completed_older_than_30d(project):
    async with async_session() as db:
        old = VisionChatSession(
            id="old-approved", project_id=project.id, state="approved",
            phase="structured", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(days=31),
        )
        db.add(old); await db.commit()
    async with async_session() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert deleted == 1
    async with async_session() as db:
        assert await db.get(VisionChatSession, "old-approved") is None


@pytest.mark.asyncio
async def test_sweep_leaves_recent_active_alone(project):
    async with async_session() as db:
        s = VisionChatSession(
            id="recent", project_id=project.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(s); await db.commit()
    async with async_session() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert cancelled == 0
    async with async_session() as db:
        refreshed = await db.get(VisionChatSession, "recent")
        assert refreshed.state == "active"
