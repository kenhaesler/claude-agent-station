from __future__ import annotations

"""Async SQLAlchemy engine, session factory, and DB initialization."""

import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

DATABASE_URL = settings.resolved_db_url


def _engine_kwargs(url: str) -> dict:
    is_pg = url.startswith("postgresql")
    kwargs: dict = {"echo": False}
    if is_pg:
        # QueuePool params; not valid for SQLite's StaticPool.
        kwargs["pool_size"] = 20
        kwargs["max_overflow"] = 10
    else:
        # SQLite uses QueuePool for file URLs and StaticPool for :memory:.
        # pool_size/max_overflow are accepted by QueuePool but rejected by
        # StaticPool, so we only add them for non-memory file URLs.
        if ":memory:" not in url:
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 0
    return kwargs


engine = create_async_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _run_sqlite_pragmas(dbapi_conn, _connection_record, *, cursor_factory=None) -> None:
    """Apply WAL + foreign-key PRAGMAs. SQLite-only; no-op on Postgres.

    Exposed at module level so tests can call it directly with a fake
    cursor factory; the previous attribute-on-function pattern was
    brittle and made the call graph hard to read.
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = (cursor_factory or (lambda c: c.cursor()))(dbapi_conn)
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL + foreign keys — SQLite-only."""
    _run_sqlite_pragmas(dbapi_conn, connection_record)


logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Run Alembic migrations against the configured database.

    `_migrate_add_columns` is retired — every column it added is encoded in
    the Alembic baseline revision (`alembic/versions/0001_baseline.py`).
    The legacy auxiliary migration (`migrations/0003_simplify_config_schema.py`)
    is a config-JSON transform, not schema; it still runs after upgrade.

    We pass ``STATION_DB_URL`` explicitly to the subprocess so that the
    Alembic process always targets the same database as the in-process engine,
    regardless of any ``STATION_DB_PATH`` mutations in the environment (e.g.
    per-test fixtures that change the path after the engine was created).
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "STATION_DB_URL": DATABASE_URL}
    # Use ``sys.executable -m alembic`` rather than ``["alembic", ...]`` so
    # we always invoke the alembic package installed alongside this Python
    # interpreter, regardless of PATH. The bare-name form breaks in
    # containers and virtualenvs where alembic is installed but not on PATH.
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_root),
        env=env,
    )

    # Config-JSON migration still applicable post-schema.
    try:
        import importlib
        mod = importlib.import_module("migrations.0003_simplify_config_schema")
        mod.run()
    except Exception as e:
        logger.debug("Config schema migration skipped: %s", e)
