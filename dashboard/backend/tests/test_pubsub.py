"""LISTEN/NOTIFY tests (#393).

Postgres tests request the ``postgres_url`` fixture, which auto-skips
when Docker is not available on the host. The SQLite tests verify the
no-op contract (``notify`` returns silently, ``listen`` exhausts).
"""
from __future__ import annotations

import asyncio
import os

import pytest

# Import app.database early so the module-level engine is created with the
# SQLite URL from conftest (before any test sets STATION_DB_URL to Postgres).
import app.database  # noqa: F401

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


def test_lifespan_starts_listen_notify_subscribers():
    """Regression guard: ``app/main.py::lifespan`` must start both
    ``_run_event_subscriber`` and ``_heartbeat_subscriber`` as background
    tasks. Without these started, the LISTEN side of LISTEN/NOTIFY is
    dead code and the Postgres-relaxed poll intervals (300s log_importer,
    60s reaper) leave the dashboard *slower* than the SQLite path.

    Source-level assertion — we don't boot the FastAPI app here.
    """
    import inspect
    from app import main
    src = inspect.getsource(main.lifespan)
    assert "_run_event_subscriber" in src, (
        "lifespan must start _run_event_subscriber as a background task (#393 PR-3)"
    )
    assert "_heartbeat_subscriber" in src, (
        "lifespan must start _heartbeat_subscriber as a background task (#393 PR-3)"
    )
    # Both must be wrapped in asyncio.create_task so they run concurrently
    # with the request loop.
    assert "asyncio.create_task(_run_event_subscriber" in src
    assert "asyncio.create_task(_heartbeat_subscriber" in src


def test_notify_uses_singleton_connection():
    """Regression guard: ``notify()`` must use the singleton connection
    via ``_get_notify_conn``, not ``asyncpg.connect`` per call.

    Original PR-3 code opened a fresh connection on every webhook event
    — ~50/sec at peak. The singleton + reconnect-on-error path is what
    makes Postgres usage scalable.
    """
    import inspect
    from app.services import pubsub
    src = inspect.getsource(pubsub.notify)
    assert "_get_notify_conn" in src, (
        "notify() must use _get_notify_conn singleton, not asyncpg.connect per call"
    )
    # Belt-and-braces: the raw asyncpg.connect call should NOT appear in
    # the notify function body (it's allowed inside listen and
    # _get_notify_conn).
    assert "asyncpg.connect" not in src


def test_asyncpg_dsn_preserves_query_params(monkeypatch):
    """``_asyncpg_dsn`` must preserve URL query parameters so production
    deployments with ``?sslmode=require`` connect over TLS instead of
    silently downgrading to plaintext.
    """
    monkeypatch.setenv(
        "STATION_DB_URL",
        "postgresql+asyncpg://u:p@h:5432/db?sslmode=require&application_name=test",
    )
    from app.services.pubsub import _asyncpg_dsn
    dsn = _asyncpg_dsn()
    assert "sslmode=require" in dsn
    assert "application_name=test" in dsn
