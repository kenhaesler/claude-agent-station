"""LISTEN/NOTIFY tests (#393).

Postgres tests are marked ``postgres_only``; on SQLite we assert the
contract is a clean no-op.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.services.pubsub import listen, notify


@pytest.mark.asyncio
async def test_notify_sqlite_noop(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "sqlite+aiosqlite:///:memory:")
    await notify("run_event", {"run_id": "x"})  # no exception


@pytest.mark.asyncio
async def test_listen_sqlite_terminates_immediately(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "sqlite+aiosqlite:///:memory:")

    async def _consume():
        async for _ in listen("run_event"):
            return "got"
        return "exhausted"

    result = await asyncio.wait_for(_consume(), timeout=1.0)
    assert result == "exhausted"


@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_notify_observed_within_one_second(postgres_url, monkeypatch):
    import uuid
    channel = f"run_event_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("STATION_DB_URL", postgres_url)

    async def consumer():
        async for msg in listen(channel):
            return msg
        return None

    consume_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)  # let listener register
    await notify(channel, {"run_id": "rt-1"})
    msg = await asyncio.wait_for(consume_task, timeout=2.0)
    assert msg == {"run_id": "rt-1"}


@pytest.mark.asyncio
async def test_webhook_router_calls_notify(monkeypatch):
    """After webhook POST, notify('run_event', ...) must be called."""
    import uuid
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient
    from app.database import Base, engine
    from app.main import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        captured: list[tuple] = []

        async def mock_notify(channel, payload):
            captured.append((channel, payload))

        with patch("app.routers.webhook.notify", new=mock_notify):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                run_id = f"rt-wh-{uuid.uuid4().hex[:8]}"
                resp = await ac.post(
                    "/api/webhook/run-event",
                    json={"event": "started", "run_id": run_id, "status": "running"},
                )
            assert resp.status_code in (200, 201, 204)

        # Verify notify was called on run_event channel
        channels = [c for c, _ in captured]
        assert "run_event" in channels, f"Expected run_event notify, got: {captured}"
        payloads = [p for c, p in captured if c == "run_event"]
        assert any(p.get("run_id") == run_id for p in payloads)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
