"""Endpoint tests for the GitHub App router."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db, monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    monkeypatch.setenv("STATION_DASHBOARD_BASE_URL", "http://localhost:8420")
    # Reload so module-level constants pick up the env vars.
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_manifest_start_returns_post_url_state_and_manifest(client):
    resp = await client.post("/api/github/app/manifest/start")
    assert resp.status_code == 200
    body = resp.json()

    assert body["post_url"].startswith("https://github.com/settings/apps/new")
    assert "state=" in body["post_url"]
    assert isinstance(body["state"], str) and len(body["state"]) >= 32
    assert "manifest" in body
    m = body["manifest"]
    assert m["name"]
    assert m["url"] == "http://localhost:8420"
    assert m["redirect_url"] == "http://localhost:8420/api/github/app/manifest/exchange"
    assert m["setup_url"] == "http://localhost:8420/api/github/app/install/callback"
    assert m["public"] is False
    assert m["default_permissions"]["contents"] == "write"
    assert m["default_permissions"]["issues"] == "write"
    assert m["default_permissions"]["pull_requests"] == "write"
    assert m["default_permissions"]["metadata"] == "read"


@pytest.mark.asyncio
async def test_manifest_start_state_is_random_per_call(client):
    a = (await client.post("/api/github/app/manifest/start")).json()["state"]
    b = (await client.post("/api/github/app/manifest/start")).json()["state"]
    assert a != b
