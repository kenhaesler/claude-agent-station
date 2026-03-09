"""Config management endpoints."""

import json
import time
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import ConfigEntry
from app.services.config_sync import _read_config_json, _write_config_json, sync_config_to_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config():
    """Get the full station config (from JSON file, source of truth)."""
    config = _read_config_json()
    return config


@router.put("")
async def update_config(body: Dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Update the full station config (writes to JSON file + syncs DB).

    Accepts the full config object. Projects are synced to the DB.
    Non-project fields (models, limits, schedule, notifications, logging) are
    written directly to JSON.
    """
    # Read current config to preserve any fields not sent by frontend
    current = _read_config_json()
    # Merge: update only keys the frontend sends
    for key in ("models", "limits", "schedule", "notifications", "logging"):
        if key in body:
            current[key] = body[key]
    # Keep _mode_options metadata
    # Don't allow projects to be overwritten from this endpoint
    # (use /api/projects for that)

    _write_config_json(current)

    # Re-sync projects from JSON -> DB (in case anything changed)
    await sync_config_to_db(db)

    logger.info("Config updated via API")
    return current


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


@router.get("/usage")
async def get_usage():
    """Get current usage tracking data (sessions used, window info)."""
    usage_path = Path(settings.log_dir) / "usage-tracking.json"
    if not usage_path.exists():
        return {
            "sessions_used": 0,
            "session_limit_24h": 50,
            "max_session_percent": 80,
            "window_start": time.time(),
            "window_remaining_hours": 24.0,
            "usage_percent": 0.0,
        }

    with open(usage_path, "r") as f:
        data = json.load(f)

    config = _read_config_json()
    limits = config.get("limits", {})
    session_limit = limits.get("session_limit_24h", 50)
    max_pct = limits.get("max_session_percent", 80)
    threshold = int(session_limit * max_pct / 100)

    sessions_used = data.get("sessions_used", 0)
    window_start = data.get("window_start", time.time())
    last_run = data.get("last_run", 0)

    now = time.time()
    elapsed = now - window_start
    remaining_hours = max(0, (86400 - elapsed) / 3600)

    # If window has expired, sessions would reset on next run
    if elapsed >= 86400:
        sessions_used = 0
        remaining_hours = 24.0

    return {
        "sessions_used": sessions_used,
        "session_limit_24h": session_limit,
        "threshold": threshold,
        "max_session_percent": max_pct,
        "window_start_ts": window_start,
        "window_remaining_hours": round(remaining_hours, 1),
        "usage_percent": round((sessions_used / session_limit * 100) if session_limit > 0 else 0, 1),
        "last_run_ts": last_run,
    }
