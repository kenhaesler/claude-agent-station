"""Async SQLAlchemy engine, session factory, and DB initialization."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import event, text

from app.config import settings

DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine = create_async_engine(DATABASE_URL, echo=False)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode for concurrent reads."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


logger = logging.getLogger(__name__)


async def _migrate_add_columns(conn) -> None:
    """Add columns that may be missing from older databases."""
    migrations = [
        ("projects", "custom_instructions", "ALTER TABLE projects ADD COLUMN custom_instructions TEXT"),
        ("projects", "setup_script", "ALTER TABLE projects ADD COLUMN setup_script TEXT"),
    ]
    for table, column, sql in migrations:
        try:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            columns = [row[1] for row in result.fetchall()]
            if column not in columns:
                await conn.execute(text(sql))
                logger.info("Migration: added %s.%s", table, column)
        except Exception as e:
            logger.debug("Migration skip %s.%s: %s", table, column, e)


async def init_db():
    """Create all tables and run migrations."""
    async with engine.begin() as conn:
        from app.models import Project, Run, ConfigEntry, Notification  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_add_columns(conn)
