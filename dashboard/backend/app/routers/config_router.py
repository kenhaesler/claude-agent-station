"""Config management endpoints."""

import json
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import ConfigEntry, Run
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


@router.get("/token-usage")
async def get_token_usage(db: AsyncSession = Depends(get_db)):
    """Get token consumption data against configured limits."""
    config = _read_config_json()
    limits = config.get("limits", {})
    token_limit_daily = limits.get("token_limit_daily", 0)
    token_limit_monthly = limits.get("token_limit_monthly", 0)
    token_reserve_percent = limits.get("token_reserve_percent", 20)

    now = datetime.now(timezone.utc)

    # Daily tokens consumed (last 24h)
    day_ago = now - timedelta(hours=24)
    daily_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_input), 0),
            func.coalesce(func.sum(Run.tokens_output), 0),
            func.coalesce(func.sum(Run.tokens_total), 0),
        ).where(Run.started_at >= day_ago)
    )
    daily_row = daily_result.one()
    daily_input = daily_row[0]
    daily_output = daily_row[1]
    daily_total = daily_row[2]

    # Monthly tokens consumed (last 30 days)
    month_ago = now - timedelta(days=30)
    monthly_result = await db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_total), 0),
        ).where(Run.started_at >= month_ago)
    )
    monthly_total = monthly_result.scalar()

    # Calculate effective limits (after reserve)
    reserve_factor = 1 - (token_reserve_percent / 100)
    effective_daily = int(token_limit_daily * reserve_factor) if token_limit_daily > 0 else 0
    effective_monthly = int(token_limit_monthly * reserve_factor) if token_limit_monthly > 0 else 0

    # Usage percentages
    daily_percent = round((daily_total / effective_daily * 100) if effective_daily > 0 else 0, 1)
    monthly_percent = round((monthly_total / effective_monthly * 100) if effective_monthly > 0 else 0, 1)

    # Can spawn check: is there enough budget for an employee (~50K tokens avg)?
    avg_employee_tokens = 50000
    can_spawn = True
    if effective_daily > 0 and (daily_total + avg_employee_tokens) > effective_daily:
        can_spawn = False
    if effective_monthly > 0 and (monthly_total + avg_employee_tokens) > effective_monthly:
        can_spawn = False

    return {
        "daily": {
            "tokens_input": daily_input,
            "tokens_output": daily_output,
            "tokens_total": daily_total,
            "limit": token_limit_daily,
            "effective_limit": effective_daily,
            "usage_percent": daily_percent,
        },
        "monthly": {
            "tokens_total": monthly_total,
            "limit": token_limit_monthly,
            "effective_limit": effective_monthly,
            "usage_percent": monthly_percent,
        },
        "token_reserve_percent": token_reserve_percent,
        "can_spawn_employee": can_spawn,
    }
