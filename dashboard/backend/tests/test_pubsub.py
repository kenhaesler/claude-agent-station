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
