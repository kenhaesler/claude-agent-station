"""Tests for API key authentication.

Covers:
- 401 when API key configured but missing from request
- 401 with wrong Bearer token
- 200 with correct Bearer token
- 200 with correct ?token= query param (SSE fallback)
- 401 with wrong ?token=
- 401 on protected endpoint with no token when key configured
- 200 on all endpoints when no API key configured (backward compat)
- /api/health always accessible
- /api/webhook/* unaffected by API key auth
- secrets.compare_digest is used for timing-safe comparison
- Empty string api_key treated as disabled
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.database import engine, Base

TEST_API_KEY = "test-secret-key-12345"


@pytest_asyncio.fixture
async def setup_db():
    """Create tables for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def auth_enabled(setup_db):
    """Enable API key auth for the test."""
    original = settings.api_key
    settings.api_key = TEST_API_KEY
    yield
    settings.api_key = original


@pytest.fixture
def auth_disabled(setup_db):
    """Disable API key auth for the test (open access)."""
    original = settings.api_key
    settings.api_key = None
    yield
    settings.api_key = original


@pytest.mark.asyncio
async def test_protected_endpoint_requires_key(auth_enabled):
    """GET /api/projects without token should return 401 when API key is configured."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/projects")
    assert resp.status_code == 401
    assert "API key required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_wrong_bearer_token_rejected(auth_enabled):
    """GET /api/projects with wrong Bearer token should return 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/projects", headers={"Authorization": "Bearer wrong-key"}
        )
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_correct_bearer_token_accepted(auth_enabled):
    """GET /api/projects with correct Bearer token should succeed."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/projects",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_correct_query_token_on_non_streaming(auth_enabled):
    """GET /api/projects?token=<correct> should succeed via query param fallback."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/projects?token={TEST_API_KEY}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wrong_query_token_rejected(auth_enabled):
    """GET /api/projects?token=<wrong> should return 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/projects?token=wrong-key")
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_no_token_returns_401(auth_enabled):
    """GET /api/projects without any auth should return 401 when key is configured."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/projects")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_open_access_when_no_key(auth_disabled):
    """GET /api/projects should succeed when no API key is configured."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/projects")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_always_accessible_with_key(auth_enabled):
    """GET /api/health should always return 200, even with API key configured."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_accessible_without_key(auth_disabled):
    """GET /api/health should return 200 without API key configured."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_unaffected_by_api_key(auth_enabled):
    """POST /api/webhook/run-event should not require API key auth."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/webhook/run-event",
            json={"event": "test", "run_id": "test-123"},
        )
    # Should not be 401 from API key auth
    # (may be 422 from webhook validation, but NOT 401 API key error)
    assert resp.status_code != 401 or "API key" not in resp.text


@pytest.mark.asyncio
async def test_timing_safe_comparison_used(auth_enabled):
    """Verify secrets.compare_digest is used for key comparison."""
    import secrets

    with patch.object(secrets, "compare_digest", return_value=True) as mock_compare:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/projects",
                headers={"Authorization": f"Bearer {TEST_API_KEY}"},
            )
        mock_compare.assert_called_once_with(TEST_API_KEY, TEST_API_KEY)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_empty_string_api_key_treated_as_disabled(setup_db):
    """When api_key is empty string, auth should be disabled."""
    original = settings.api_key
    settings.api_key = ""
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/projects")
        assert resp.status_code == 200
    finally:
        settings.api_key = original
