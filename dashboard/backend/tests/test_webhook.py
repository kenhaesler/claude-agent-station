"""Tests for the webhook endpoint authentication.

Covers:
- POST /api/webhook/run-event returns 401 when secret is set and header is missing
- POST /api/webhook/run-event returns 401 when secret is set and header is wrong
- POST /api/webhook/run-event succeeds when correct X-Webhook-Token is supplied
- POST /api/webhook/run-event succeeds when no secret is configured (backward compat)
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import engine, Base


VALID_EVENT = {
    "event": "run_start",
    "run_id": "run-test-123",
    "project": "owner/repo",
}


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
# Auth enforcement when webhook_secret is set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_missing_token_when_secret_set(client: AsyncClient):
    """Request without X-Webhook-Token header should get 401."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 401
    assert "Invalid or missing webhook token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_rejects_wrong_token_when_secret_set(client: AsyncClient):
    """Request with incorrect X-Webhook-Token header should get 401."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post(
            "/api/webhook/run-event",
            json=VALID_EVENT,
            headers={"X-Webhook-Token": "wrong-token"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_accepts_correct_token(client: AsyncClient):
    """Request with correct X-Webhook-Token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post(
            "/api/webhook/run-event",
            json=VALID_EVENT,
            headers={"X-Webhook-Token": "my-secret-token"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["run_id"] == "run-test-123"


# ---------------------------------------------------------------------------
# Backward compatibility — no secret configured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_secret_allows_unauthenticated(client: AsyncClient):
    """When webhook_secret is empty/None, requests without token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = None
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_empty_secret_allows_unauthenticated(client: AsyncClient):
    """When webhook_secret is an empty string, requests without token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = ""
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
