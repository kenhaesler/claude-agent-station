"""Tests for queue orphan recovery, purge, and state transitions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import QueueItem, Run

# ---------------------------------------------------------------------------
# Fixtures – in-memory SQLite database
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture()
async def db(session_factory):
    async with session_factory() as sess:
        yield sess


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

    async def test_orphaned_items_recovered_when_run_reaped(self, db):
        from app.services.stale_run_reaper import reap_stale_runs

        _make_run(db, run_id="run-DEAD", status="running")
        _make_queue_item(db, state="assigned", run_id="run-DEAD", assigned_to=1)
        _make_queue_item(db, state="in_progress", run_id="run-DEAD", assigned_to=1)
        _make_queue_item(db, state="review", run_id="run-DEAD", assigned_to=1)
        # This completed item should NOT be touched
        _make_queue_item(db, state="completed", run_id="run-DEAD", assigned_to=1)
        await db.commit()

        # Patch ``_is_orchestrator_process_alive`` to False — the production
        # implementation calls ``pgrep -f station_orchestrator`` which matches
        # ANY host process whose cmdline contains that substring (other
        # Claude Code agents, IDEs, build scripts, etc.). Without this patch
        # the test is flaky: when pgrep finds a stray match the reaper short-
        # circuits and returns 0. See issue #407.
        with patch("app.services.stale_run_reaper.get_agent_status",
                    new_callable=AsyncMock, return_value={"service_active": False}), \
             patch("app.services.stale_run_reaper._is_orchestrator_process_alive",
                    return_value=False), \
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

    async def test_no_orphans_when_service_active(self, db):
        from app.services.stale_run_reaper import reap_stale_runs

        _make_run(db, run_id="run-ALIVE", status="running")
        _make_queue_item(db, state="assigned", run_id="run-ALIVE")
        await db.commit()

        with patch("app.services.stale_run_reaper.get_agent_status",
                    new_callable=AsyncMock, return_value={"service_active": True}):
            count = await reap_stale_runs(db)

        assert count == 0

        # Item still assigned
        result = await db.execute(select(QueueItem).where(QueueItem.state == "assigned"))
        assert len(result.scalars().all()) == 1


# ---------------------------------------------------------------------------
# Test: Queue purge endpoint
# ---------------------------------------------------------------------------

class TestQueuePurge:
    """POST /api/queue/purge should delete old completed/failed items."""

    async def test_purge_removes_old_items(self, db):
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

    async def test_delete_completed_item(self, db):
        item = _make_queue_item(db, state="completed")
        await db.commit()
        await db.refresh(item)

        from app.routers.queue import delete_queue_item
        await delete_queue_item(item.id, db=db)

        result = await db.execute(select(QueueItem).where(QueueItem.id == item.id))
        assert result.scalar_one_or_none() is None

    async def test_delete_in_progress_blocked(self, db):
        from fastapi import HTTPException

        item = _make_queue_item(db, state="in_progress")
        await db.commit()
        await db.refresh(item)

        from app.routers.queue import delete_queue_item
        with pytest.raises(HTTPException) as exc_info:
            await delete_queue_item(item.id, db=db)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Test: Update with explicit null clears fields
# ---------------------------------------------------------------------------

class TestUpdateExplicitNull:
    """PUT with explicit null values should clear fields."""

    async def test_explicit_null_clears_run_id(self, db):
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
