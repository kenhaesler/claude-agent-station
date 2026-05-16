"""Reaper must use service_control (not pgrep + systemctl) so it works
in compose where the orchestrator is in a sibling container."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.models import Run
from app.services.stale_run_reaper import (
    UNKNOWN_RUN_REAP_AGE_MINUTES,
    reap_stale_runs,
)


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
    # Patch ``_is_orchestrator_process_alive`` to False — the production
    # implementation calls ``pgrep -f station_orchestrator`` which matches
    # ANY host process whose cmdline contains that substring (other Claude
    # Code agents, IDEs, build scripts, etc.). Without this patch the test
    # is flaky on dev workstations. See issue #407.
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status), \
         patch("app.services.stale_run_reaper._is_orchestrator_process_alive",
               return_value=False):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (await s.execute(select(Run).where(Run.run_id == stale_run))).scalar_one()
    assert n == 1
    assert row.status == "interrupted"


# ── issue #268: stranded ``unknown`` rows ─────────────────────────────────


@pytest_asyncio.fixture
async def old_unknown_run(setup_db):
    """An ``unknown`` row whose started_at is well past the reap threshold.

    Mirrors the production stuck-rows from issue #268: orchestrator exited
    with no eligible issues, log_importer wrote the placeholder, no terminal
    webhook ever updated the status.
    """
    rid = "run-unknown-old-001"
    async with async_session() as s:
        # Push started_at twice the threshold into the past so we are well
        # outside any clock-skew window.
        old = datetime.now(timezone.utc) - timedelta(
            minutes=UNKNOWN_RUN_REAP_AGE_MINUTES * 2,
        )
        s.add(Run(run_id=rid, status="unknown", started_at=old))
        await s.commit()
    return rid


@pytest_asyncio.fixture
async def fresh_unknown_run(setup_db):
    """An ``unknown`` row that was just inserted — must NOT be reaped.

    The launcher's own ``finished`` webhook should be allowed to land first.
    """
    rid = "run-unknown-fresh-001"
    async with async_session() as s:
        s.add(Run(run_id=rid, status="unknown", started_at=datetime.now(timezone.utc)))
        await s.commit()
    return rid


@pytest.mark.asyncio
async def test_reaper_marks_old_unknown_runs_interrupted(old_unknown_run):
    """Issue #268 acceptance criterion: the reaper must catch ``unknown``
    rows older than the threshold whose orchestrator process is dead."""
    mock_status = AsyncMock(return_value={"service_active": False})
    # Patch ``_is_orchestrator_process_alive`` to False (see issue #407 —
    # pgrep -f station_orchestrator can match unrelated host processes).
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status), \
         patch("app.services.stale_run_reaper._is_orchestrator_process_alive",
               return_value=False):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (
                await s.execute(select(Run).where(Run.run_id == old_unknown_run))
            ).scalar_one()
    assert n == 1
    assert row.status == "interrupted"
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_reaper_skips_fresh_unknown_runs(fresh_unknown_run):
    """A freshly-inserted ``unknown`` row may still be in the brief window
    before its terminal webhook lands. Don't race it."""
    mock_status = AsyncMock(return_value={"service_active": False})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (
                await s.execute(select(Run).where(Run.run_id == fresh_unknown_run))
            ).scalar_one()
    assert n == 0
    assert row.status == "unknown"
    assert row.finished_at is None


@pytest.mark.asyncio
async def test_reaper_skips_unknown_runs_when_agent_active(old_unknown_run):
    """Even an ancient ``unknown`` row stays put if the agent is alive — the
    live launcher might still own that run_id."""
    mock_status = AsyncMock(return_value={"service_active": True})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (
                await s.execute(select(Run).where(Run.run_id == old_unknown_run))
            ).scalar_one()
    assert n == 0
    assert row.status == "unknown"
