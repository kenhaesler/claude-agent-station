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


@pytest.mark.asyncio
async def test_sweep_removes_orphan_upload_dirs(async_session_factory, tmp_path, monkeypatch):
    """Upload dirs for sessions that don't exist (or aren't active) get removed."""
    import uuid as _uuid
    monkeypatch.setattr("app.services.vision_cleanup._upload_root", lambda: tmp_path)
    from app.services.vision_cleanup import sweep_stale_sessions as _sweep

    # Insert a Project row so FK constraints are satisfied.
    async with async_session_factory() as db:
        p = Project(repo=f"owner/cleanup-{_uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        project_id = p.id

    # 1. Fully orphan dir (no matching session row)
    orphan_id = _uuid.uuid4().hex
    (tmp_path / orphan_id).mkdir()
    (tmp_path / orphan_id / "f.txt").write_text("x")

    # 2. Dir for an approved session — should be removed
    async with async_session_factory() as db:
        old = VisionChatSession(
            id=_uuid.uuid4().hex, project_id=project_id, state="approved",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(old)
        await db.commit()
        (tmp_path / old.id).mkdir()
        (tmp_path / old.id / "g.txt").write_text("x")
        old_id = old.id

    # 3. Dir for an active session — must NOT be removed
    async with async_session_factory() as db:
        live = VisionChatSession(
            id=_uuid.uuid4().hex, project_id=project_id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(live)
        await db.commit()
        (tmp_path / live.id).mkdir()
        (tmp_path / live.id / "h.txt").write_text("x")
        live_id = live.id

    async with async_session_factory() as db:
        await _sweep(db)

    assert not (tmp_path / orphan_id).exists()
    assert not (tmp_path / old_id).exists()
    assert (tmp_path / live_id).exists()
