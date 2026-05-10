"""Tests for conflict_resolution_* webhook events."""

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
async def test_started_event_persists(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "conflict_resolution_started",
        "run_id": "run-cr-001",
        "project": "owner/repo",
        "branch": "feature/x",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (await db.execute(
            select(AgentEvent).where(AgentEvent.event_type == "conflict_resolution_started")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].run_id == "run-cr-001"


@pytest.mark.asyncio
async def test_completed_event_carries_phase_and_tokens(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "conflict_resolution_completed",
        "run_id": "run-cr-002",
        "project": "owner/repo",
        "branch": "feature/x",
        "phase": "llm",
        "count": 1500,  # tokens_total
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (await db.execute(
            select(AgentEvent).where(AgentEvent.run_id == "run-cr-002")
        )).scalars().all()
    assert len(rows) == 1
    data = json.loads(rows[0].event_data)
    assert data["phase"] == "llm"
    assert data["count"] == 1500
    assert data["branch"] == "feature/x"
