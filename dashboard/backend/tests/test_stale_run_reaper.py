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


@pytest.mark.asyncio
async def test_reap_expired_pending_placeholder(setup_db):
    """A pending placeholder older than PENDING_REAP_AGE_SECONDS gets
    marked failed (issue #346 safety net)."""
    from datetime import datetime, timedelta, timezone
    from app.models import Run
    from app.services.stale_run_reaper import (
        reap_stale_runs, PENDING_REAP_AGE_SECONDS,
    )
    from sqlalchemy import select
    from unittest.mock import patch, AsyncMock

    too_old = datetime.now(timezone.utc) - timedelta(
        seconds=PENDING_REAP_AGE_SECONDS + 30
    )
    async with async_session() as db:
        db.add(Run(run_id="run-stale-pending", status="pending",
                   started_at=too_old))
        await db.commit()

    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": False}):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)
    assert reaped >= 1
    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-stale-pending")
        )).scalar_one()
        assert row.status == "failed"


@pytest.mark.asyncio
async def test_reap_stale_heartbeat_runs_when_launcher_alive(setup_db):
    """The whole point of the heartbeat reap: it must run EVEN when the
    launcher reports an active service, because that's the case where
    the orchestrator may have died silently between events while the
    launcher container itself stays up. See PR #351 review."""
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
        db.add(Run(run_id="run-silent-death", status="running",
                   started_at=too_old, last_event_at=too_old))
        await db.commit()

    # Launcher reports service ALIVE — this used to short-circuit the
    # heartbeat reap before the PR-#351-review fix.
    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": True}):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)

    assert reaped >= 1
    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-silent-death")
        )).scalar_one()
        assert row.status == "interrupted"


# ── issue #407 positive coverage: tie-breaker branch ──────────────────────


@pytest.mark.asyncio
async def test_reaper_skips_when_orchestrator_process_alive(setup_db):
    """Tie-breaker coverage (issue #407): when ``deploy_mode() == 'systemd'``
    and the launcher reports ``service_active=False`` but pgrep finds a
    live ``station_orchestrator`` process, the main sweep must NOT reap.

    Every other test in the suite mocks ``_is_orchestrator_process_alive``
    to False to neutralise host-process flakiness, so without this test
    the True branch of the tie-breaker has zero coverage.

    The run is seeded WITHOUT ``last_event_at`` so the heartbeat reap
    (which runs unconditionally before the tie-breaker) does not pick it
    up; only the main service-inactive sweep would, and the tie-breaker
    must skip that sweep.
    """
    from app.services.stale_run_reaper import reap_stale_runs

    async with async_session() as db:
        db.add(Run(
            run_id="run-orchestrator-alive",
            status="running",
            started_at=datetime.now(timezone.utc),
            # last_event_at left NULL so the heartbeat sweep can't claim it.
        ))
        await db.commit()

    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": False}), \
         patch("app.services.stale_run_reaper.deploy_mode",
               return_value="systemd"), \
         patch("app.services.stale_run_reaper._is_orchestrator_process_alive",
               return_value=True):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)

    assert reaped == 0, "tie-breaker must short-circuit the main sweep"

    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-orchestrator-alive")
        )).scalar_one()
        assert row.status == "running", (
            "seeded run must NOT be marked interrupted while orchestrator "
            "process is alive"
        )
        assert row.finished_at is None
