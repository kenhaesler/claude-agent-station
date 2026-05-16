"""Tests for the ``heartbeat`` webhook event handler.

Heartbeat events are emitted by the orchestrator's control poll loop
(agent/station_orchestrator._control_poll_loop) every 30s so the
dashboard's stale-run reaper sees liveness during long quiet windows.

Without them, an Agent Teams run with a Sonnet 4.6 manager-review turn
that takes 90-120s would be marked ``interrupted`` even though it's
healthy. Regression guards for run-20260516T005654Z post-mortem.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import Run


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_heartbeat_is_recognized_by_normalizer():
    """``heartbeat`` must be in the recognized event mapping so it
    doesn't fall through to ``handle_unknown`` (which mutates
    ``run.status`` if the event carries one)."""
    from app.routers.webhook import _normalize_event_name
    assert _normalize_event_name("heartbeat") == "heartbeat"


@pytest.mark.asyncio
async def test_heartbeat_bumps_last_event_at(client):
    """A heartbeat for a known run row must update ``last_event_at`` so
    the stale-run reaper sees liveness, without changing ``status``."""
    stale = datetime.now(timezone.utc) - timedelta(seconds=300)
    async with async_session() as db:
        db.add(Run(
            run_id="run-hb-1",
            status="running",
            started_at=stale,
            last_event_at=stale,
        ))
        await db.commit()

    response = await client.post(
        "/api/webhook/run-event",
        json={"event": "heartbeat", "run_id": "run-hb-1"},
    )
    assert response.status_code == 200, response.text

    async with async_session() as db:
        from sqlalchemy import select
        row = (await db.execute(select(Run).where(Run.run_id == "run-hb-1"))).scalar_one()
        assert row.status == "running", (
            f"heartbeat must not change status; got {row.status!r}"
        )
        assert row.last_event_at is not None
        # SQLite drops tzinfo on round-trip; compare naive-to-naive.
        row_naive = row.last_event_at.replace(tzinfo=None) if row.last_event_at.tzinfo else row.last_event_at
        stale_naive = stale.replace(tzinfo=None)
        assert row_naive > stale_naive, (
            "heartbeat must bump last_event_at past the stale value"
        )


@pytest.mark.asyncio
async def test_heartbeat_does_not_resurrect_terminal_run(client):
    """Edge case: a heartbeat arriving after a run has already finished
    must not bump it back to running. Status preservation: heartbeat
    only bumps ``last_event_at``, never mutates ``status``."""
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        db.add(Run(
            run_id="run-hb-done",
            status="completed",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=1),
            last_event_at=now - timedelta(minutes=1),
        ))
        await db.commit()

    response = await client.post(
        "/api/webhook/run-event",
        json={"event": "heartbeat", "run_id": "run-hb-done"},
    )
    assert response.status_code == 200

    async with async_session() as db:
        from sqlalchemy import select
        row = (await db.execute(select(Run).where(Run.run_id == "run-hb-done"))).scalar_one()
        assert row.status == "completed", (
            f"terminal status must survive a stray heartbeat; got {row.status!r}"
        )


@pytest.mark.asyncio
async def test_heartbeat_for_unknown_run_id_is_a_no_op(client):
    """A heartbeat for a run_id that doesn't exist yet (orchestrator
    mid-spawn) must not crash and must not create a phantom row."""
    response = await client.post(
        "/api/webhook/run-event",
        json={"event": "heartbeat", "run_id": "run-doesnt-exist"},
    )
    assert response.status_code == 200, response.text

    async with async_session() as db:
        from sqlalchemy import select
        rows = (await db.execute(select(Run).where(Run.run_id == "run-doesnt-exist"))).all()
        assert rows == [], (
            "heartbeat must not create a phantom run row when no row exists yet"
        )
