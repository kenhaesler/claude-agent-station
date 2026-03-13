"""Tests for the DB-backed TaskDAG.

Uses an in-memory SQLite database so no files are created.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent.coordinator.db import Base, DbCoordinatorTask
from agent.coordinator.dag import TaskDAG, TaskStatus


@pytest_asyncio.fixture
async def session_factory():
    """Create an in-memory SQLite engine + tables for each test."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sf

    await engine.dispose()


# -------------------------------------------------------------------
# Basic CRUD
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_and_get_task(session_factory):
    dag = TaskDAG("run-1", "owner/repo", session_factory)
    t = await dag.add_task(title="Task A", description="desc A")
    assert t.id == "task-run-1-0"
    assert t.title == "Task A"
    assert t.status == TaskStatus.READY  # no deps → immediately ready

    fetched = await dag.get_task(t.id)
    assert fetched.id == t.id
    assert fetched.description == "desc A"


@pytest.mark.asyncio
async def test_task_count(session_factory):
    dag = TaskDAG("run-2", "owner/repo", session_factory)
    assert await dag.task_count() == 0
    await dag.add_task(title="A")
    await dag.add_task(title="B")
    assert await dag.task_count() == 2


@pytest.mark.asyncio
async def test_sequence_numbering(session_factory):
    dag = TaskDAG("run-3", "owner/repo", session_factory)
    t0 = await dag.add_task(title="Zero")
    t1 = await dag.add_task(title="One")
    assert t0.id == "task-run-3-0"
    assert t1.id == "task-run-3-1"


# -------------------------------------------------------------------
# Status transitions
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_running(session_factory):
    dag = TaskDAG("run-4", "owner/repo", session_factory)
    t = await dag.add_task(title="Work")
    await dag.mark_running(t.id, employee_index=0, workspace="/tmp/ws")

    updated = await dag.get_task(t.id)
    assert updated.status == TaskStatus.RUNNING
    assert updated.employee_index == 0
    assert updated.workspace == "/tmp/ws"
    assert updated.started_at is not None


@pytest.mark.asyncio
async def test_mark_completed(session_factory):
    dag = TaskDAG("run-5", "owner/repo", session_factory)
    t = await dag.add_task(title="Work")
    await dag.mark_running(t.id, 0, "/tmp/ws")
    newly_ready = await dag.mark_completed(t.id, exit_code=0)

    updated = await dag.get_task(t.id)
    assert updated.status == TaskStatus.COMPLETED
    assert updated.exit_code == 0
    assert updated.finished_at is not None
    # No other tasks, so nothing newly ready
    assert newly_ready == []


@pytest.mark.asyncio
async def test_mark_failed(session_factory):
    dag = TaskDAG("run-6", "owner/repo", session_factory)
    t = await dag.add_task(title="Work")
    await dag.mark_running(t.id, 0, "/tmp/ws")
    await dag.mark_failed(t.id, error="boom", exit_code=1)

    updated = await dag.get_task(t.id)
    assert updated.status == TaskStatus.FAILED
    assert updated.error_message == "boom"
    assert updated.exit_code == 1


# -------------------------------------------------------------------
# Dependency resolution
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependency_readiness(session_factory):
    dag = TaskDAG("run-7", "owner/repo", session_factory)
    t0 = await dag.add_task(title="First")
    t1 = await dag.add_task(title="Second", depends_on=[t0.id])

    # t0 should be ready (no deps), t1 should be pending
    t1_fresh = await dag.get_task(t1.id)
    assert t1_fresh.status == TaskStatus.PENDING

    ready = await dag.ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == t0.id

    # Complete t0 → t1 should become ready
    await dag.mark_running(t0.id, 0, "/tmp/ws")
    newly_ready = await dag.mark_completed(t0.id)
    assert len(newly_ready) == 1
    assert newly_ready[0].id == t1.id


@pytest.mark.asyncio
async def test_cascade_block(session_factory):
    dag = TaskDAG("run-8", "owner/repo", session_factory)
    t0 = await dag.add_task(title="Root")
    t1 = await dag.add_task(title="Child", depends_on=[t0.id])
    t2 = await dag.add_task(title="Grandchild", depends_on=[t1.id])

    # Fail t0 → t1 and t2 should be blocked
    await dag.mark_running(t0.id, 0, "/tmp/ws")
    await dag.mark_failed(t0.id, "oops")

    t1_fresh = await dag.get_task(t1.id)
    t2_fresh = await dag.get_task(t2.id)
    assert t1_fresh.status == TaskStatus.BLOCKED
    assert t2_fresh.status == TaskStatus.BLOCKED
    assert "Blocked by failed task" in t1_fresh.error_message
    assert "Blocked by failed task" in t2_fresh.error_message


# -------------------------------------------------------------------
# all_done / summary
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_done(session_factory):
    dag = TaskDAG("run-9", "owner/repo", session_factory)
    assert await dag.all_done()  # no tasks at all → done

    t = await dag.add_task(title="Work")
    assert not await dag.all_done()  # ready task

    await dag.mark_running(t.id, 0, "/tmp/ws")
    assert not await dag.all_done()  # running

    await dag.mark_completed(t.id)
    assert await dag.all_done()  # completed


@pytest.mark.asyncio
async def test_all_done_with_blocked(session_factory):
    dag = TaskDAG("run-10", "owner/repo", session_factory)
    t0 = await dag.add_task(title="Root")
    await dag.add_task(title="Child", depends_on=[t0.id])

    await dag.mark_running(t0.id, 0, "/tmp/ws")
    await dag.mark_failed(t0.id, "nope")

    # Root is failed, child is blocked — both are terminal states
    assert await dag.all_done()


@pytest.mark.asyncio
async def test_summary(session_factory):
    dag = TaskDAG("run-11", "owner/repo", session_factory)
    t0 = await dag.add_task(title="A")
    t1 = await dag.add_task(title="B")
    await dag.mark_running(t0.id, 0, "/tmp/ws")

    s = await dag.summary()
    assert s.get("running") == 1
    assert s.get("ready") == 1


# -------------------------------------------------------------------
# Crash recovery
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_from_crash(session_factory):
    dag = TaskDAG("run-12", "owner/repo", session_factory)
    t0 = await dag.add_task(title="A")
    t1 = await dag.add_task(title="B")

    await dag.mark_running(t0.id, 0, "/tmp/ws0")
    await dag.mark_running(t1.id, 1, "/tmp/ws1")

    # Simulate crash recovery
    reset = await dag.recover_from_crash()
    assert reset == 2

    t0_fresh = await dag.get_task(t0.id)
    t1_fresh = await dag.get_task(t1.id)
    assert t0_fresh.status == TaskStatus.READY
    assert t1_fresh.status == TaskStatus.READY
    assert t0_fresh.employee_index is None
    assert t0_fresh.workspace is None


# -------------------------------------------------------------------
# Touched files + to_dict
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_touched_files(session_factory):
    dag = TaskDAG("run-13", "owner/repo", session_factory)
    t = await dag.add_task(title="Work")
    await dag.update_touched_files(t.id, ["a.py", "b.py"])

    updated = await dag.get_task(t.id)
    assert updated.touched_files == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_to_dict(session_factory):
    dag = TaskDAG("run-14", "owner/repo", session_factory)
    await dag.add_task(title="A")
    await dag.add_task(title="B")

    d = await dag.to_dict()
    assert d["run_id"] == "run-14"
    assert d["project_repo"] == "owner/repo"
    assert len(d["tasks"]) == 2
    assert "ready" in d["summary"]


# -------------------------------------------------------------------
# single_task classmethod
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_task(session_factory):
    dag = await TaskDAG.single_task(
        "run-15", "owner/repo", session_factory,
        title="Solo task",
        description="Do it all",
        issue_number=42,
    )
    assert await dag.task_count() == 1
    tasks = await dag.ready_tasks()
    assert len(tasks) == 1
    assert tasks[0].title == "Solo task"
    assert tasks[0].issue_number == 42


# -------------------------------------------------------------------
# Run isolation
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_isolation(session_factory):
    """Tasks from different runs don't interfere."""
    dag_a = TaskDAG("run-A", "owner/repo", session_factory)
    dag_b = TaskDAG("run-B", "owner/repo", session_factory)

    await dag_a.add_task(title="Task A")
    await dag_b.add_task(title="Task B1")
    await dag_b.add_task(title="Task B2")

    assert await dag_a.task_count() == 1
    assert await dag_b.task_count() == 2

    a_ready = await dag_a.ready_tasks()
    assert len(a_ready) == 1
    assert a_ready[0].title == "Task A"
