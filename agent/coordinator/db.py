"""Async SQLAlchemy engine for the coordinator process.

Connects to the same SQLite DB as the dashboard but with its own engine.
Tables are created by the dashboard on startup — we only connect here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import Column, DateTime, Integer, Text, event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# Module-level state (set by init_db)
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


class DbCoordinatorTask(Base):
    """Mirrors dashboard CoordinatorTask — same table, same columns."""

    __tablename__ = "coordinator_tasks"

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    project_repo = Column(Text, nullable=False)
    issue_number = Column(Integer, nullable=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Text, default="pending")
    employee_index = Column(Integer, nullable=True)
    depends_on = Column(Text, nullable=True)  # JSON array of task IDs
    workspace = Column(Text, nullable=True)
    expected_files = Column(Text, nullable=True)  # JSON array
    touched_files = Column(Text, nullable=True)  # JSON array
    exit_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    result_summary = Column(Text, nullable=True)
    log_path = Column(Text, nullable=True)
    branch = Column(Text, nullable=True)
    dag_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class DbCoordinatorMessage(Base):
    """Mirrors dashboard CoordinatorMessage."""

    __tablename__ = "coordinator_messages"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    task_id = Column(Text, nullable=True)
    direction = Column(Text, nullable=False)
    message_type = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    employee_index = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True)


async def init_db(db_path: str) -> async_sessionmaker[AsyncSession]:
    """Create engine + session factory for the coordinator.

    The tables already exist (created by dashboard init).
    We just connect and enable WAL mode.
    """
    global _session_factory

    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    # Verify connectivity
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT 1"))
        result.fetchone()

    logger.info("Coordinator DB connected: %s", db_path)
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session from the coordinator's session factory."""
    if _session_factory is None:
        raise RuntimeError("Coordinator DB not initialized — call init_db() first")
    async with _session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory (for passing to TaskDAG)."""
    if _session_factory is None:
        raise RuntimeError("Coordinator DB not initialized — call init_db() first")
    return _session_factory
