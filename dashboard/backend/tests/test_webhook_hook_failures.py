"""Tests for hook_failures webhook event persistence.

The orchestrator posts a `hook_failures` event when the bundled Claude CLI's
hook callback to Python fails mid-run (see agent/audit_hook.py). The webhook
router persists each event as an AgentEvent row with
event_type='hook_callback_failure'.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import AgentEvent


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


@pytest.mark.asyncio
async def test_hook_failures_persists_to_agent_events(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "hook_failures",
        "run_id": "run-hook-001",
        "project": "owner/repo",
        "count": 3,
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.event_type == "hook_callback_failure")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].run_id == "run-hook-001"
    data = json.loads(rows[0].event_data)
    assert data["project"] == "owner/repo"
    assert data["count"] == 3


@pytest.mark.asyncio
async def test_hook_failures_agent_id_defaults_to_lead(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "hook_failures",
        "run_id": "run-hook-002",
        "project": "owner/repo",
        "count": 1,
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == "run-hook-002")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].agent_id == "lead"


@pytest.mark.asyncio
async def test_hook_failures_count_is_optional(client):
    """A payload without an explicit count should still persist; count=None."""
    r = await client.post("/api/webhook/run-event", json={
        "event": "hook_failures",
        "run_id": "run-hook-003",
        "project": "owner/repo",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == "run-hook-003")
            )
        ).scalars().all()

    assert len(rows) == 1
    data = json.loads(rows[0].event_data)
    assert data["count"] is None
