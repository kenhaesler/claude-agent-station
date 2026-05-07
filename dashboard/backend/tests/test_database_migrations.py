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
