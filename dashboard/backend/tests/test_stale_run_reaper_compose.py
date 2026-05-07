"""Reaper must use service_control (not pgrep + systemctl) so it works
in compose where the orchestrator is in a sibling container."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.models import Run
from app.services.stale_run_reaper import reap_stale_runs


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def stale_run(setup_db):
    async with async_session() as s:
        r = Run(run_id="run-stale-001", status="running", started_at=datetime.now(timezone.utc))
        s.add(r)
        await s.commit()
    return "run-stale-001"


@pytest.mark.asyncio
async def test_reaper_does_nothing_when_agent_active(stale_run):
    mock_status = AsyncMock(return_value={"service_active": True})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
    assert n == 0


@pytest.mark.asyncio
async def test_reaper_marks_runs_interrupted_when_agent_inactive(stale_run):
    mock_status = AsyncMock(return_value={"service_active": False})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (await s.execute(select(Run).where(Run.run_id == stale_run))).scalar_one()
    assert n == 1
    assert row.status == "interrupted"
