"""Endpoint tests for the provider-keys router (OpenAI / Gemini API keys)."""

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
    """A clean ASGI client with the keys file pointed at a tmp path."""
    monkeypatch.setenv("STATION_PROVIDER_KEYS_PATH", str(tmp_path / "provider_keys.json"))

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_returns_unconfigured_when_empty(client):
    resp = await client.get("/api/provider-keys")
    assert resp.status_code == 200
    body = resp.json()
    assert body["openai"]["configured"] is False
    assert body["openai"]["masked_key"] is None
    assert body["openai"]["last_updated"] is None
    assert body["gemini"]["configured"] is False
    assert body["gemini"]["masked_key"] is None


@pytest.mark.asyncio
async def test_put_openai_persists_and_redacts_in_response(client, tmp_path):
    raw = "sk-test123ABCDEFG"
    resp = await client.put("/api/provider-keys/openai", json={"key": raw})
    assert resp.status_code == 200
    body = resp.json()

    assert body["configured"] is True
    assert body["last_updated"]  # ISO timestamp populated
    # Masked form: sk- prefix preserved, suffix preserved, middle hidden,
    # and crucially the raw key must not appear anywhere in the response.
    masked = body["masked_key"]
    assert masked is not None
    assert raw not in masked
    assert masked.startswith("sk-tes")
    assert masked.endswith("DEFG")
    assert "…" in masked

    # And a follow-up GET reflects the same state.
    snap = (await client.get("/api/provider-keys")).json()
    assert snap["openai"]["configured"] is True
    assert snap["openai"]["masked_key"] == masked
    assert snap["gemini"]["configured"] is False


@pytest.mark.asyncio
async def test_put_gemini_uses_aiza_aware_mask(client):
    raw = "AIzaSyD-EXAMPLE-KEY-WXYZ"
    resp = await client.put("/api/provider-keys/gemini", json={"key": raw})
    assert resp.status_code == 200
    masked = resp.json()["masked_key"]
    assert masked.startswith("AIzaSy")
    assert masked.endswith("WXYZ")
    assert raw not in masked


@pytest.mark.asyncio
async def test_delete_clears_configured_state(client):
    await client.put("/api/provider-keys/openai", json={"key": "sk-test123ABCDEFG"})
    resp = await client.delete("/api/provider-keys/openai")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["masked_key"] is None
    assert body["last_updated"] is None

    # GET confirms.
    snap = (await client.get("/api/provider-keys")).json()
    assert snap["openai"]["configured"] is False


@pytest.mark.asyncio
async def test_unknown_provider_rejected_with_400(client):
    put_resp = await client.put("/api/provider-keys/anthropic", json={"key": "sk-x"})
    assert put_resp.status_code == 400

    del_resp = await client.delete("/api/provider-keys/mistral")
    assert del_resp.status_code == 400


@pytest.mark.asyncio
async def test_empty_key_rejected(client):
    resp = await client.put("/api/provider-keys/openai", json={"key": "   "})
    # Pydantic validator fires before the route body, so we get a 422.
    assert resp.status_code in (400, 422)
