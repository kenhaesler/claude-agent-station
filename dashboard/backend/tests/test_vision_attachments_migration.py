"""Alembic migration test for vision_chat_attachments (0005).

Verifies that after `alembic upgrade head`:
- Table ``vision_chat_attachments`` exists.
- All required columns are present.
- Index ``ix_vision_chat_attachments_session_id`` exists.

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


BACKEND_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_COLUMNS = {
    "id",
    "session_id",
    "filename",
    "mime_type",
    "size_bytes",
    "disk_path",
    "extracted_text",
    "sent_at",
    "created_at",
}
EXPECTED_INDEX = "ix_vision_chat_attachments_session_id"


@pytest.mark.asyncio
async def test_0005_vision_chat_attachments_table_exists():
    """After upgrade head, vision_chat_attachments table must exist."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        env = {**os.environ, "STATION_DB_URL": f"sqlite+aiosqlite:///{db_path}"}
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            table_names = {r[0] for r in rows.fetchall()}
        await engine.dispose()

        assert "vision_chat_attachments" in table_names, (
            f"vision_chat_attachments missing; tables found: {sorted(table_names)}"
        )
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_0005_vision_chat_attachments_columns():
    """After upgrade head, vision_chat_attachments must have all required columns."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        env = {**os.environ, "STATION_DB_URL": f"sqlite+aiosqlite:///{db_path}"}
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql(
                "PRAGMA table_info(vision_chat_attachments)"
            )
            col_names = {r[1] for r in rows.fetchall()}
        await engine.dispose()

        missing = EXPECTED_COLUMNS - col_names
        assert not missing, (
            f"Missing columns in vision_chat_attachments: {missing}"
        )
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_0005_vision_chat_attachments_index():
    """After upgrade head, ix_vision_chat_attachments_session_id index must exist."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        env = {**os.environ, "STATION_DB_URL": f"sqlite+aiosqlite:///{db_path}"}
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
            )
            index_names = {r[0] for r in rows.fetchall()}
        await engine.dispose()

        assert EXPECTED_INDEX in index_names, (
            f"{EXPECTED_INDEX} missing; indexes found: {sorted(index_names)}"
        )
    finally:
        os.unlink(db_path)
