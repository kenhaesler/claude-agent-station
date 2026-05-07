"""Tests for vision_misalignment webhook event persistence."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import AgentEvent


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_vision_misalignment_event_persists_to_agent_events(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "vision_misalignment",
        "run_id": "run-test-001",
        "issue_number": 42,
        "violated_section": "non_goals",
        "quote": "Multi-tenant is out of scope.",
        "plan_excerpt": "I'll add tenant isolation…",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.event_type == "vision_misalignment")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].run_id == "run-test-001"


@pytest.mark.asyncio
async def test_vision_misalignment_event_data_fields(client):
    """Event data JSON should contain all vision-specific fields."""
    import json

    r = await client.post("/api/webhook/run-event", json={
        "event": "vision_misalignment",
        "run_id": "run-test-002",
        "issue_number": 7,
        "violated_section": "non_goals",
        "quote": "We won't do X.",
        "plan_excerpt": "Step 1: do X.",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == "run-test-002")
            )
        ).scalars().all()

    assert len(rows) == 1
    data = json.loads(rows[0].event_data)
    assert data["violated_section"] == "non_goals"
    assert data["quote"] == "We won't do X."
    assert data["plan_excerpt"] == "Step 1: do X."
    assert data["issue_number"] == 7


@pytest.mark.asyncio
async def test_vision_misalignment_agent_id_defaults_to_lead(client):
    """When no agent_id is provided, it should default to 'lead'."""
    r = await client.post("/api/webhook/run-event", json={
        "event": "vision_misalignment",
        "run_id": "run-test-003",
        "violated_section": "constraints",
        "quote": "No external APIs.",
        "plan_excerpt": "Call Stripe API.",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == "run-test-003")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].agent_id == "lead"


@pytest.mark.asyncio
async def test_vision_misalignment_with_explicit_agent_id(client):
    """Explicit agent_id in payload should be stored on the event."""
    r = await client.post("/api/webhook/run-event", json={
        "event": "vision_misalignment",
        "run_id": "run-test-004",
        "agent_id": "lead-agent-7",
        "violated_section": "goals",
        "quote": "Only CLI support.",
        "plan_excerpt": "Build a GUI.",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.run_id == "run-test-004")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].agent_id == "lead-agent-7"
