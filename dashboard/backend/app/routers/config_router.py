"""Config management endpoints."""

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import ConfigEntry
from app.services.config_sync import _read_config_json

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config():
    """Get the full station config (from JSON file, source of truth)."""
    config = _read_config_json()
    return config


@router.get("/db")
async def get_config_db(db: AsyncSession = Depends(get_db)):
    """Get all config entries from DB (key-value store for non-project settings)."""
    result = await db.execute(select(ConfigEntry))
    entries = result.scalars().all()
    return {e.key: json.loads(e.value) if e.value else None for e in entries}


@router.put("/{key}")
async def set_config_entry(key: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Set a config entry in the DB key-value store."""
    value = json.dumps(body.get("value"))

    result = await db.execute(select(ConfigEntry).where(ConfigEntry.key == key))
    entry = result.scalar_one_or_none()

    if entry:
        entry.value = value
    else:
        entry = ConfigEntry(key=key, value=value)
        db.add(entry)

    await db.commit()
    return {"key": key, "value": body.get("value")}
