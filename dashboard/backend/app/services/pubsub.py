"""Cross-process pub/sub over Postgres LISTEN/NOTIFY (#393).

Production path: asyncpg LISTEN on a named channel; emit via NOTIFY.
SQLite path: ``notify`` is a no-op; ``listen`` immediately exhausts.
Callers degrade to polling for SQLite.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import os

import asyncpg
from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)


def _resolved_db_url() -> str:
    """Return the DB URL to use, reading env vars fresh each call.

    Reads ``STATION_DB_URL`` from the environment on each call so that
    tests can override it with ``os.environ[...]`` after module import.
    Falls back to ``STATION_DB_PATH`` when the URL is not set.
    """
    db_url = os.environ.get("STATION_DB_URL", "")
    if db_url:
        return db_url
    db_path = os.environ.get("STATION_DB_PATH", "/var/lib/claude-agent-station/station.db")
    return f"sqlite+aiosqlite:///{db_path}"


def _is_postgres() -> bool:
    return _resolved_db_url().startswith("postgresql")


def _asyncpg_dsn() -> str:
    url = make_url(_resolved_db_url())
    return (
        f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/{url.database}"
    )


async def notify(channel: str, payload: dict) -> None:
    if not _is_postgres():
        return
    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        await conn.execute("SELECT pg_notify($1, $2)", channel, json.dumps(payload))
    finally:
        await conn.close()


async def listen(channel: str) -> AsyncIterator[dict]:
    if not _is_postgres():
        return  # immediately exhaust
        yield  # pragma: no cover  (makes this an async generator)
    conn = await asyncpg.connect(_asyncpg_dsn())
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _on_notify(_c, _pid, _channel, payload):
        try:
            queue.put_nowait(json.loads(payload))
        except json.JSONDecodeError:
            logger.warning("pubsub: dropping non-JSON payload on %s", _channel)

    await conn.add_listener(channel, _on_notify)
    try:
        while True:
            yield await queue.get()
    finally:
        await conn.remove_listener(channel, _on_notify)
        await conn.close()
