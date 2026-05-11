"""Tests for stale run reaper — heartbeat-based fast reap (issue #348)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.models import Run


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_reap_stale_heartbeat(setup_db):
    """A running row with no event for > timeout AND idle launcher
    gets marked interrupted (issue #348)."""
    from datetime import datetime, timedelta, timezone
    from app.models import Run
    from app.services.stale_run_reaper import (
        reap_stale_runs, ACTIVE_HEARTBEAT_TIMEOUT_SECONDS,
    )
    from sqlalchemy import select
    from unittest.mock import patch, AsyncMock

    too_old = datetime.now(timezone.utc) - timedelta(
        seconds=ACTIVE_HEARTBEAT_TIMEOUT_SECONDS + 10
    )
    async with async_session() as db:
        db.add(Run(run_id="run-stuck-heartbeat", status="running",
                   started_at=too_old, last_event_at=too_old))
        await db.commit()

    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": False}):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)
    assert reaped >= 1
    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-stuck-heartbeat")
        )).scalar_one()
        assert row.status == "interrupted"
