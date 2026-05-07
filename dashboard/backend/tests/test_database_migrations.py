"""Tests for Project vision cache column migrations (Phase 1 — vision authoring)."""

import pytest
from sqlalchemy import text

from app.database import engine, init_db


@pytest.mark.asyncio
async def test_vision_cache_columns_exist():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(projects)"))
        columns = {row[1] for row in result.fetchall()}
    assert "vision_cached_sha" in columns
    assert "vision_cached_body" in columns
    assert "vision_cached_at" in columns


@pytest.mark.asyncio
async def test_vision_chat_sessions_table_exists():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vision_chat_sessions'"
        ))
        rows = result.fetchall()
    assert len(rows) == 1, "vision_chat_sessions table missing"


@pytest.mark.asyncio
async def test_vision_chat_session_has_required_columns():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(vision_chat_sessions)"))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        columns = {row[1] for row in result.fetchall()}
    for col in ("id", "project_id", "state", "phase", "coverage",
                "sdk_session_id", "messages", "assembled",
                "created_at", "updated_at"):
        assert col in columns, f"missing column: {col}"
