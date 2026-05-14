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
async def test_notify_sqlite_noop():
    os.environ["STATION_DB_URL"] = "sqlite+aiosqlite:///:memory:"
    await notify("run_event", {"run_id": "x"})  # no exception


@pytest.mark.asyncio
async def test_listen_sqlite_terminates_immediately():
    os.environ["STATION_DB_URL"] = "sqlite+aiosqlite:///:memory:"
    async def _consume():
        async for _ in listen("run_event"):
            return "got"
        return "exhausted"

    result = await asyncio.wait_for(_consume(), timeout=1.0)
    assert result == "exhausted"


@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_notify_observed_within_one_second(postgres_url):
    import uuid
    channel = f"run_event_{uuid.uuid4().hex[:8]}"
    os.environ["STATION_DB_URL"] = postgres_url

    async def consumer():
        async for msg in listen(channel):
            return msg
        return None

    consume_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)  # let listener register
    await notify(channel, {"run_id": "rt-1"})
    msg = await asyncio.wait_for(consume_task, timeout=2.0)
    assert msg == {"run_id": "rt-1"}
