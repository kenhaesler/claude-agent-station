"""End-to-end timeline test — at least one event per source per run (#387)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.database import Base, async_session, engine
from app.models import (
    AgentEvent,
    AuditEntry,
    ConflictResolution,
    CoordinatorTask,
    Run,
)
from app.services.run_timeline import build_timeline


@pytest_asyncio.fixture(scope="module")
async def setup_db():
    """Create tables once for all tests in this module."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_full_run_yields_every_source(setup_db):
    run_id = "run-tl-integration-1"
    async with async_session() as db:
        db.add(Run(
            run_id=run_id,
            status="success",
            branch="feature/integration",
            started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 13, 15, 45, 0, tzinfo=timezone.utc),
            verdict="PR",
        ))
        db.add(AgentEvent(
            workflow_id="wfi",
            run_id=run_id,
            agent_id="manager",
            event_type="verdict_execute",
            event_data='{"verdict":"PR"}',
            created_at=datetime(2026, 5, 13, 15, 40, 0, tzinfo=timezone.utc),
        ))
        db.add(AuditEntry(
            idempotency_key="i-k1",
            run_id=run_id,
            actor="lead",
            action_kind="tool.bash",
            status="ok",
            started_at=datetime(2026, 5, 13, 15, 10, 0, tzinfo=timezone.utc),
        ))
        db.add(CoordinatorTask(
            id="i-t1",
            run_id=run_id,
            project_repo="x/y",
            status="completed",
            started_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
            claimed_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 13, 15, 15, 0, tzinfo=timezone.utc),
            teammate_agent_id="backend",
            title="integration task",
        ))
        db.add(ConflictResolution(
            branch="feature/integration",
            repo="x/y",
            phase_reached="llm",
            outcome="resolved",
            triggered_by="pre_pr",
            started_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 13, 15, 30, 30, tzinfo=timezone.utc),
        ))
        await db.commit()

    async with async_session() as db:
        page = await build_timeline(
            db, run_id, kinds=None, since=None, until=None, limit=500, cursor=None
        )

    kinds_seen = {e.kind for e in page.events}
    assert kinds_seen == {"lifecycle", "tool", "teammate", "verdict", "conflict"}
    times = [e.t for e in page.events]
    assert times == sorted(times)
    assert page.events[0].event == "run_start"
    assert page.events[-1].event in {"run_complete", "conflict.resolved", "verdict_execute"}
