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

    # Use a file URL for SQLite (pool_size/max_overflow valid on QueuePool only).
    sqlite_kw = _engine_kwargs("sqlite+aiosqlite:////tmp/test.db")
    # In-memory SQLite skips pool params (StaticPool doesn't accept them).
    mem_kw = _engine_kwargs("sqlite+aiosqlite:///:memory:")
    pg_kw = _engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert sqlite_kw["pool_size"] == 5
    assert sqlite_kw["max_overflow"] == 0
    assert "pool_size" not in mem_kw
    assert pg_kw["pool_size"] == 20
    assert pg_kw["max_overflow"] == 10


def test_asyncpg_and_alembic_importable():
    import alembic  # noqa: F401
    import asyncpg  # noqa: F401


def test_jsontype_uses_jsonb_on_postgres():
    from app.models import JsonType  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import JSONB

    pg_impl = JsonType.dialect_impl(
        __import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    )
    assert isinstance(pg_impl, JSONB), f"expected JSONB, got {pg_impl.__class__.__name__}"


def test_jsontype_uses_json_on_sqlite():
    from app.models import JsonType  # noqa: PLC0415
    from sqlalchemy import JSON

    sq_impl = JsonType.dialect_impl(
        __import__("sqlalchemy.dialects.sqlite", fromlist=["dialect"]).dialect()
    )
    # On SQLite, JSON is used (no JSONB variant). The impl is a JSON subclass.
    assert isinstance(sq_impl, JSON), f"expected JSON, got {sq_impl.__class__.__name__}"


def test_decode_event_data_handles_text_and_dict():
    from app.services.json_compat import decode_event_data
    assert decode_event_data('{"a": 1}') == {"a": 1}
    assert decode_event_data({"a": 1}) == {"a": 1}
    assert decode_event_data(None) is None
    assert decode_event_data("not-json") is None


@pytest.mark.asyncio
async def test_smoke_insert_select(async_session_factory):
    """Parametrized over sqlite/postgres via async_session_factory fixture."""
    from app.models import Run
    from datetime import datetime

    # TODO(#414): switch to ``datetime.now(timezone.utc)`` once #393 PR-4
    # migrates the schema to ``DateTime(timezone=True)``. Today the columns
    # are declared as naive (TIMESTAMP WITHOUT TIME ZONE on Postgres);
    # Postgres rejects tz-aware writes against them while SQLite is lax. The
    # naive ``utcnow()`` here is the workaround — it also emits a Python 3.12
    # DeprecationWarning that goes away once #414 lands.
    async with async_session_factory() as db:
        db.add(Run(run_id="run-smoke", status="running",
                   started_at=datetime.utcnow()))
        await db.commit()

    from sqlalchemy import select
    async with async_session_factory() as db:
        row = (await db.execute(select(Run).where(Run.run_id == "run-smoke"))).scalar_one()
        assert row.status == "running"


def test_no_raw_jsonloads_on_jsonb_columns():
    import pathlib

    backend_app = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in backend_app.rglob("*.py"):
        text = path.read_text()
        for needle in ("event_data", "action_detail", "employee_report", "verdict_detail"):
            if "json.loads(" in text and needle in text:
                # Allow services/json_compat.py through.
                if path.name == "json_compat.py":
                    continue
                lines = [
                    ln for ln in text.splitlines()
                    if "json.loads(" in ln and needle in ln
                ]
                offenders.extend(f"{path}: {ln.strip()}" for ln in lines)
    assert offenders == [], "use decode_event_data:\n" + "\n".join(offenders)
