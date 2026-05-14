"""Cross-process pub/sub over Postgres LISTEN/NOTIFY (#393).

Production path: asyncpg LISTEN on a named channel; emit via NOTIFY.
SQLite path: ``notify`` is a no-op; ``listen`` immediately exhausts.
Callers degrade to polling for SQLite.

The ``notify`` path reuses a singleton asyncpg connection across all
publishers in the process. Opening a fresh connection per webhook event
(the original PR-3 shape) was ~1-5 ms per call and at the spec's "peak
~50 events/sec" volume meant 50 connect/teardown cycles per second.
The singleton is established lazily on the first ``notify`` call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

import asyncpg
from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)

# Postgres pg_notify has a payload size limit of 7,899 bytes. We keep
# our own ceiling well under that so JSON-encoding overhead can't push
# past it.
_MAX_NOTIFY_PAYLOAD_BYTES = 6_000


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
    """Convert SQLAlchemy URL to an asyncpg DSN, preserving query params.

    ``postgresql+asyncpg://...?sslmode=require`` must keep ``sslmode``
    so production deployments with TLS work. The raw f-string approach
    in the initial PR-3 commit silently dropped the query string and
    would have connected in plaintext.
    """
    url = make_url(_resolved_db_url())
    dsn = (
        f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/{url.database}"
    )
    # Re-append any query parameters (sslmode, application_name, etc.).
    if url.query:
        qs = "&".join(f"{k}={v}" for k, v in url.query.items())
        dsn = f"{dsn}?{qs}"
    return dsn


# Singleton publisher connection. Lazily established; reconnects on (a) a
# closed connection (transient network failure), or (b) a URL change (test
# environments that switch backends within one process, e.g. parametrized
# tests). The URL is tracked alongside the connection so a stale singleton
# pointing at a torn-down test container gets replaced on the next call.
_notify_conn: asyncpg.Connection | None = None
_notify_conn_dsn: str | None = None
_notify_conn_lock = asyncio.Lock()


async def _get_notify_conn() -> asyncpg.Connection:
    """Return the singleton publisher connection, opening it on first use.

    Reconnects when either the existing connection is closed OR the
    target DSN has changed since the singleton was opened.
    """
    global _notify_conn, _notify_conn_dsn
    target_dsn = _asyncpg_dsn()
    async with _notify_conn_lock:
        needs_reconnect = (
            _notify_conn is None
            or _notify_conn.is_closed()
            or _notify_conn_dsn != target_dsn
        )
        if needs_reconnect:
            if _notify_conn is not None and not _notify_conn.is_closed():
                try:
                    await _notify_conn.close()
                except Exception:  # noqa: BLE001
                    pass
            _notify_conn = await asyncpg.connect(target_dsn)
            _notify_conn_dsn = target_dsn
        return _notify_conn


async def notify(channel: str, payload: dict) -> None:
    """Broadcast ``payload`` on the named channel via Postgres NOTIFY.

    No-op on SQLite. A transient connection error (network blip,
    container churn in tests, stale singleton after a Postgres
    restart) is caught, the singleton is force-reset, and the call is
    retried once. A second failure logs a warning and returns — the
    caller should not be required to handle pubsub failures.
    Oversized payloads are dropped with a warning rather than raising.
    """
    if not _is_postgres():
        return
    payload_json = json.dumps(payload)
    if len(payload_json.encode("utf-8")) > _MAX_NOTIFY_PAYLOAD_BYTES:
        logger.warning(
            "pubsub: dropping oversized %s payload (%d bytes > %d limit)",
            channel, len(payload_json.encode("utf-8")), _MAX_NOTIFY_PAYLOAD_BYTES,
        )
        return
    for attempt in (1, 2):
        try:
            conn = await _get_notify_conn()
            await conn.execute("SELECT pg_notify($1, $2)", channel, payload_json)
            return
        except (asyncpg.PostgresError, OSError) as exc:
            # Force reconnect; second iteration retries once with a fresh
            # connection. If the second attempt also fails, log + return.
            await _reset_notify_conn_locked()
            if attempt == 2:
                logger.warning(
                    "pubsub: NOTIFY %s failed after retry: %s", channel, exc,
                )


async def _reset_notify_conn_locked() -> None:
    """Internal: drop the singleton publisher connection."""
    global _notify_conn, _notify_conn_dsn
    async with _notify_conn_lock:
        if _notify_conn is not None and not _notify_conn.is_closed():
            try:
                await _notify_conn.close()
            except Exception:  # noqa: BLE001
                pass
        _notify_conn = None
        _notify_conn_dsn = None


async def listen(channel: str) -> AsyncIterator[dict]:
    """Async-iterate JSON payloads sent to ``channel`` via Postgres NOTIFY.

    No-op on SQLite — the generator returns immediately so subscribers
    that ``async for`` over it exhaust cleanly without waiting.

    Each ``listen`` invocation opens its own asyncpg connection because
    asyncpg's ``add_listener`` is per-connection and we don't want a
    single subscriber's slow handler to block another subscriber.
    """
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


async def reset_notify_connection() -> None:
    """Close the singleton publisher connection.

    Intended for test teardown — production code does not need to call
    this since the singleton self-heals on the next ``notify`` after a
    transient failure or URL change.
    """
    global _notify_conn, _notify_conn_dsn
    async with _notify_conn_lock:
        if _notify_conn is not None:
            try:
                if not _notify_conn.is_closed():
                    await _notify_conn.close()
            except Exception:  # noqa: BLE001
                pass
            _notify_conn = None
            _notify_conn_dsn = None
