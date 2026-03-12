"""Tests for queue orphan recovery, purge, and state transitions."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.database import Base
from app.models import QueueItem, Run


# ---------------------------------------------------------------------------
# Fixtures – in-memory SQLite database
# ---------------------------------------------------------------------------

@pytest.fixture()
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def engine(event_loop):
    eng = event_loop.run_until_complete(_make_engine())
    yield eng
    event_loop.run_until_complete(eng.dispose())


async def _make_engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng


@pytest.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def db(event_loop, session_factory):
    sess = event_loop.run_until_complete(session_factory().__aenter__())
    yield sess
    event_loop.run_until_complete(sess.__aexit__(None, None, None))


def run_async(coro):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _make_queue_item(db, **kwargs):
    defaults = dict(
        project_repo="owner/repo",
        issue_number=1,
        issue_title="Test issue",
        state="pending",
        priority=0,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    defaults.update(kwargs)
    item = QueueItem(**defaults)
    db.add(item)
    return item


def _make_run(db, **kwargs):
    defaults = dict(
        run_id="run-20260312T120000Z",
        status="running",
        started_at=_utcnow(),
    )
    defaults.update(kwargs)
    run = Run(**defaults)
    db.add(run)
    return run


# ---------------------------------------------------------------------------
# Test: Stale run reaper recovers orphaned queue items
# ---------------------------------------------------------------------------

class TestStaleRunReaperQueueRecovery:
    """stale_run_reaper should transition orphaned queue items to pending."""

    def test_orphaned_items_recovered_when_run_reaped(self, db):
        from app.services.stale_run_reaper import reap_stale_runs

        async def _run():
            run = _make_run(db, run_id="run-DEAD", status="running")
            _make_queue_item(db, state="assigned", run_id="run-DEAD", assigned_to=1)
            _make_queue_item(db, state="in_progress", run_id="run-DEAD", assigned_to=1)
            _make_queue_item(db, state="review", run_id="run-DEAD", assigned_to=1)
            # This completed item should NOT be touched
            _make_queue_item(db, state="completed", run_id="run-DEAD", assigned_to=1)
            await db.commit()

            with patch("app.services.stale_run_reaper.get_service_status",
                        new_callable=AsyncMock, return_value={"service_active": False}), \
                 patch("app.services.stale_run_reaper.event_bus_publish",
                        new_callable=AsyncMock), \
                 patch("app.services.stale_run_reaper.send_notification",
                        new_callable=AsyncMock):
                count = await reap_stale_runs(db)

            assert count == 1  # 1 run reaped

            result = await db.execute(select(QueueItem).where(QueueItem.state == "pending"))
            pending = result.scalars().all()
            assert len(pending) == 3

            for item in pending:
                assert item.run_id is None
                assert item.assigned_to is None

            # Completed item unchanged
            result = await db.execute(select(QueueItem).where(QueueItem.state == "completed"))
            completed = result.scalars().all()
            assert len(completed) == 1

        run_async(_run())

    def test_no_orphans_when_service_active(self, db):
        from app.services.stale_run_reaper import reap_stale_runs

        async def _run():
            _make_run(db, run_id="run-ALIVE", status="running")
            _make_queue_item(db, state="assigned", run_id="run-ALIVE")
            await db.commit()

            with patch("app.services.stale_run_reaper.get_service_status",
                        new_callable=AsyncMock, return_value={"service_active": True}):
                count = await reap_stale_runs(db)

            assert count == 0

            # Item still assigned
            result = await db.execute(select(QueueItem).where(QueueItem.state == "assigned"))
            assert len(result.scalars().all()) == 1

        run_async(_run())


# ---------------------------------------------------------------------------
# Test: Queue purge endpoint
# ---------------------------------------------------------------------------

class TestQueuePurge:
    """POST /api/queue/purge should delete old completed/failed items."""

    def test_purge_removes_old_items(self, db):
        async def _run():
            old = _utcnow() - timedelta(days=10)
            recent = _utcnow() - timedelta(days=1)

            _make_queue_item(db, state="completed", updated_at=old)
            _make_queue_item(db, state="failed", updated_at=old)
            _make_queue_item(db, state="completed", updated_at=recent)  # too new
            _make_queue_item(db, state="pending", updated_at=old)  # wrong state
            await db.commit()

            from app.routers.queue import purge_completed
            # Call the endpoint function directly with our db
            result = await purge_completed(max_age_days=7, db=db)
            assert result["purged"] == 2

            # Verify remaining items
            all_result = await db.execute(select(QueueItem))
            remaining = all_result.scalars().all()
            assert len(remaining) == 2
            states = {item.state for item in remaining}
            assert states == {"completed", "pending"}

        run_async(_run())


# ---------------------------------------------------------------------------
# Test: State transitions allow orphan recovery
# ---------------------------------------------------------------------------

class TestStateTransitions:
    """Verify in_progress→pending and review→pending are valid."""

    def test_in_progress_to_pending_allowed(self):
        from app.routers.queue import TRANSITIONS
        assert "pending" in TRANSITIONS["in_progress"]

    def test_review_to_pending_allowed(self):
        from app.routers.queue import TRANSITIONS
        assert "pending" in TRANSITIONS["review"]

    def test_assigned_to_pending_allowed(self):
        from app.routers.queue import TRANSITIONS
        assert "pending" in TRANSITIONS["assigned"]


# ---------------------------------------------------------------------------
# Test: DELETE allows completed items
# ---------------------------------------------------------------------------

class TestDeleteCompleted:
    """DELETE /api/queue/{id} should now accept completed items."""

    def test_delete_completed_item(self, db):
        async def _run():
            item = _make_queue_item(db, state="completed")
            await db.commit()
            await db.refresh(item)

            from app.routers.queue import delete_queue_item
            await delete_queue_item(item.id, db=db)

            result = await db.execute(select(QueueItem).where(QueueItem.id == item.id))
            assert result.scalar_one_or_none() is None

        run_async(_run())

    def test_delete_in_progress_blocked(self, db):
        from fastapi import HTTPException

        async def _run():
            item = _make_queue_item(db, state="in_progress")
            await db.commit()
            await db.refresh(item)

            from app.routers.queue import delete_queue_item
            with pytest.raises(HTTPException) as exc_info:
                await delete_queue_item(item.id, db=db)
            assert exc_info.value.status_code == 400

        run_async(_run())


# ---------------------------------------------------------------------------
# Test: Update with explicit null clears fields
# ---------------------------------------------------------------------------

class TestUpdateExplicitNull:
    """PUT with explicit null values should clear fields."""

    def test_explicit_null_clears_run_id(self, db):
        async def _run():
            item = _make_queue_item(db, state="assigned", run_id="run-OLD", assigned_to=1)
            await db.commit()
            await db.refresh(item)

            from app.routers.queue import update_queue_item
            from app.schemas import QueueItemUpdate

            with patch("app.routers.queue.event_bus_publish", new_callable=AsyncMock):
                data = QueueItemUpdate(state="pending", run_id=None, assigned_to=None)
                # Manually set model_fields_set to include the null fields
                data.model_fields_set.add("run_id")
                data.model_fields_set.add("assigned_to")
                result = await update_queue_item(item.id, data=data, db=db)

            assert result.run_id is None
            assert result.assigned_to is None
            assert result.state == "pending"

        run_async(_run())
