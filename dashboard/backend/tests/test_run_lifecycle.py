"""Tests for run_lifecycle handle_finished with vision-bootstrap fields."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.models import CoordinatorTask, Run
from app.schemas import WebhookRunEvent
from app.services.run_lifecycle import handle_finished


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_handle_finished_persists_vision_bootstrap_fields(setup_db):
    event = WebhookRunEvent(
        event="finished",
        run_id="run-vb-test-1",
        project="laboef1900/next-itsm",
        mode="vision-bootstrap",
        status="success",
        vision_bootstrap_count=3,
        vision_bootstrap_proposals=[
            {"number": 101, "title": "Add metrics dashboard", "url": "https://github.com/x/y/issues/101"},
            {"number": 102, "title": "Document API", "url": "https://github.com/x/y/issues/102"},
            {"number": 103, "title": "Add CI", "url": "https://github.com/x/y/issues/103"},
        ],
    )
    async with async_session() as db:
        run = await handle_finished(db, event, project_id=None, run=None)
        await db.commit()
        await db.refresh(run)
        assert run.mode == "vision-bootstrap"
        assert run.vision_bootstrap_count == 3
        proposals = json.loads(run.vision_bootstrap_proposals)
        assert len(proposals) == 3
        assert proposals[0]["number"] == 101


@pytest.mark.asyncio
async def test_handle_finished_orphans_running_coordinator_tasks(setup_db):
    """When a Run finalises, any of its coordinator_tasks left in 'running' or
    'claimed' must be cascaded to 'orphaned'. Fixes the zombie-task bug
    (issue #345) where /api/runs/active-employees surfaces stale rows."""
    run_id = "run-orphan-test-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime.now(timezone.utc)))
        db.add(CoordinatorTask(id="t-zombie-run", run_id=run_id,
                               project_repo="x/y", title="zombie run task",
                               status="running",
                               started_at=datetime.now(timezone.utc)))
        db.add(CoordinatorTask(id="t-zombie-claim", run_id=run_id,
                               project_repo="x/y", title="zombie claim task",
                               status="claimed",
                               started_at=datetime.now(timezone.utc)))
        db.add(CoordinatorTask(id="t-other-run", run_id="run-other",
                               project_repo="x/y", title="other run task",
                               status="running",
                               started_at=datetime.now(timezone.utc)))
        await db.commit()

    event = WebhookRunEvent(event="finished", run_id=run_id, status="success")
    async with async_session() as db:
        await handle_finished(db, event, project_id=None,
                              run=(await db.execute(
                                  select(Run).where(Run.run_id == run_id)
                              )).scalar_one())
        await db.commit()

    async with async_session() as db:
        rows = (await db.execute(select(CoordinatorTask))).scalars().all()
        by_id = {r.id: r for r in rows}
        assert by_id["t-zombie-run"].status == "orphaned"
        assert by_id["t-zombie-claim"].status == "orphaned"
        assert by_id["t-zombie-claim"].claimed_at is None
        assert by_id["t-other-run"].status == "running"
