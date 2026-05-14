"""STATION_DB_URL / STATION_DB_PATH resolution tests (#393)."""
from __future__ import annotations

import pytest

from app.config import Settings


def test_resolved_db_url_prefers_db_url():
    s = Settings(
        db_path="/tmp/legacy.db",
        db_url="postgresql+asyncpg://u:p@h/db",
    )
    assert s.resolved_db_url == "postgresql+asyncpg://u:p@h/db"


def test_resolved_db_url_falls_back_to_sqlite_path():
    s = Settings(db_path="/tmp/x.db", db_url=None)
    assert s.resolved_db_url == "sqlite+aiosqlite:////tmp/x.db"


def test_resolved_db_url_blank_falls_back_to_sqlite():
    s = Settings(db_path="/tmp/y.db", db_url="")
    assert s.resolved_db_url == "sqlite+aiosqlite:////tmp/y.db"


from sqlalchemy.ext.asyncio import create_async_engine


def test_pragma_listener_no_op_on_postgres(monkeypatch):
    """Postgres connections must not run sqlite PRAGMAs."""
    from app.database import _run_sqlite_pragmas

    class FakeCursor:
        def __init__(self):
            self.ran: list[str] = []

        def execute(self, sql):
            self.ran.append(sql)

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    class FakeEngine:
        class dialect:
            name = "postgresql"

    # Override the engine reference used by the listener guard.
    import app.database as mod
    monkeypatch.setattr(mod, "engine", FakeEngine())
    cur = FakeCursor()
    _run_sqlite_pragmas(FakeConn(), None, cursor_factory=lambda c: cur)
    assert cur.ran == [], "should not run pragmas under postgres"


def test_pool_size_scales_by_dialect():
    from app.database import _engine_kwargs

    sqlite_kw = _engine_kwargs("sqlite+aiosqlite:///:memory:")
    pg_kw = _engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert sqlite_kw["pool_size"] == 5
    assert sqlite_kw["max_overflow"] == 0
    assert pg_kw["pool_size"] == 20
    assert pg_kw["max_overflow"] == 10


def test_asyncpg_and_alembic_importable():
    import alembic  # noqa: F401
    import asyncpg  # noqa: F401
