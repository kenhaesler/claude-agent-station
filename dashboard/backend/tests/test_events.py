"""Tests for the SSE events router.

Covers:
- GET /api/events/subscribers — returns subscriber count
- GET /api/events/stream — returns SSE response with correct headers
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


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


# ---------------------------------------------------------------------------
# GET /api/events/subscribers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_subscriber_count_zero(client):
    """GET /api/events/subscribers should return 0 when no subscribers."""
    with patch("app.routers.events.subscriber_count", return_value=0):
        resp = await client.get("/api/events/subscribers")
    assert resp.status_code == 200
    assert resp.json() == {"subscribers": 0}


@pytest.mark.asyncio
async def test_get_subscriber_count_nonzero(client):
    """GET /api/events/subscribers should return correct count."""
    with patch("app.routers.events.subscriber_count", return_value=3):
        resp = await client.get("/api/events/subscribers")
    assert resp.status_code == 200
    assert resp.json() == {"subscribers": 3}


# ---------------------------------------------------------------------------
# GET /api/events/stream — SSE response headers and initial data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_stream_content_type(client):
    """GET /api/events/stream should return text/event-stream content type."""

    async def _mock_subscribe():
        """Yield one event then stop (so the stream terminates)."""
        yield {"type": "test", "data": {"msg": "hello"}}

    with patch("app.routers.events.subscribe", _mock_subscribe):
        resp = await client.get("/api/events/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_event_stream_initial_comment(client):
    """GET /api/events/stream should start with a connected comment."""

    async def _mock_subscribe():
        yield {"type": "ping", "data": {}}

    with patch("app.routers.events.subscribe", _mock_subscribe):
        resp = await client.get("/api/events/stream")
    assert resp.status_code == 200
    body = resp.text
    assert ": connected" in body


@pytest.mark.asyncio
async def test_event_stream_formats_sse(client):
    """GET /api/events/stream should format events as SSE."""

    async def _mock_subscribe():
        yield {"type": "run_start", "data": {"run_id": "r-001"}}

    with patch("app.routers.events.subscribe", _mock_subscribe):
        resp = await client.get("/api/events/stream")
    body = resp.text
    assert "event: run_start" in body
    assert "r-001" in body
