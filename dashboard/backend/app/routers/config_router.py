from __future__ import annotations

"""Config management endpoints."""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import ConfigEntry, Run
from app.services.config_sync import _read_config_json, _write_config_json, sync_config_to_db
from app.services.notifier import send_test_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# ── Old field names that were removed in the simplified schema ──
_REMOVED_LIMIT_FIELDS = {
    "token_limit_daily",
    "token_limit_monthly",
    "token_reserve_percent",
    "session_limit_24h",
    "max_session_percent",
}

# ── Default values for the new simplified fields ──
_NEW_LIMIT_DEFAULTS = {
    "max_usage_percent": 80,
    "reserve_percent": 20,
}


def _migrate_limits_in_memory(limits: dict[str, Any]) -> dict[str, Any]:
    """Migrate old limit fields to new schema in-memory.

    If old fields are present, derive new field values from them and strip
    the old fields.  This provides backward compatibility for configs that
    haven't been migrated yet.
    """
    migrated = dict(limits)

    # Derive max_usage_percent from old max_session_percent if present
    if "max_session_percent" in migrated and "max_usage_percent" not in migrated:
        migrated["max_usage_percent"] = migrated["max_session_percent"]

    # Derive reserve_percent from old token_reserve_percent if present
    if "token_reserve_percent" in migrated and "reserve_percent" not in migrated:
        migrated["reserve_percent"] = migrated["token_reserve_percent"]

    # Ensure new fields have defaults
    for field, default in _NEW_LIMIT_DEFAULTS.items():
        if field not in migrated:
            migrated[field] = default

    # Remove old fields
    for field in _REMOVED_LIMIT_FIELDS:
        migrated.pop(field, None)

    return migrated


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize config by migrating any old-style limits to new schema."""
    if "limits" in config:
        config["limits"] = _migrate_limits_in_memory(config["limits"])
    return config


@router.get("")
async def get_config():
    """Get the full station config (from JSON file, source of truth).

    Automatically migrates old limit fields to the new simplified schema
    in the response.  The on-disk file is not modified until a PUT.
    """
    config = await asyncio.to_thread(_read_config_json)
    return _normalize_config(config)


@router.put("")
async def update_config(body: dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Update the full station config (writes to JSON file + syncs DB).

    Accepts the full config object. Projects are synced to the DB.
    Non-project fields (models, limits, schedule, notifications, logging) are
    written directly to JSON.

    If the incoming limits still contain old field names they are migrated
    automatically before writing.
    """
    # Read current config to preserve any fields not sent by frontend
    current = await asyncio.to_thread(_read_config_json)
    # Merge: update only keys the frontend sends
    for key in ("models", "limits", "schedule", "notifications", "logging"):
        if key in body:
            current[key] = body[key]

    # Normalize limits (migrate old -> new) before persisting
    current = _normalize_config(current)

    await asyncio.to_thread(_write_config_json, current)

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


# Allowed config keys for the key-value store
ALLOWED_CONFIG_KEYS = {
    "notifications",
    "schedule",
    "dashboard_theme",
    "prompt_override_manager",
    "prompt_override_employee",
    "prompt_override_analyst",
    "prompt_override_planner",
    "prompt_override_assigner",
}


@router.put("/{key}")
async def set_config_entry(key: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Set a config entry in the DB key-value store."""
    # Allow prompt overrides dynamically
    if key not in ALLOWED_CONFIG_KEYS and not key.startswith("prompt_override_"):
        raise HTTPException(
            status_code=400,
            detail=f"Config key '{key}' is not in the allowed list. Allowed keys: {sorted(ALLOWED_CONFIG_KEYS)}",
        )
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
    """Get current usage tracking data.

    Uses the new simplified max_usage_percent field. Falls back gracefully
    to old field names for unmigrated configs.
    """
    config = await asyncio.to_thread(_read_config_json)
    limits = config.get("limits", {})

    # Use new field, fall back to old for backward compat
    max_usage_pct = limits.get(
        "max_usage_percent",
        limits.get("max_session_percent", _NEW_LIMIT_DEFAULTS["max_usage_percent"]),
    )

    usage_path = Path(settings.log_dir) / "usage-tracking.json"
    if not usage_path.exists():
        return {
            "sessions_used": 0,
            "max_usage_percent": max_usage_pct,
            "window_start": time.time(),
            "window_remaining_hours": 24.0,
            "usage_percent": 0.0,
        }

    def _read_usage():
        with open(usage_path) as f:
            return json.load(f)

    data = await asyncio.to_thread(_read_usage)

    sessions_used = data.get("sessions_used", 0)
    window_start = data.get("window_start", time.time())
    last_run = data.get("last_run", 0)
    plan_limit = data.get("plan_limit", 0)  # from Claude plan detection

    now = time.time()
    elapsed = now - window_start
    remaining_hours = max(0, (86400 - elapsed) / 3600)

    # If window has expired, sessions would reset on next run
    if elapsed >= 86400:
        sessions_used = 0
        remaining_hours = 24.0

    # Calculate usage percent against plan limit if known, else raw count
    if plan_limit > 0:
        usage_percent = round(sessions_used / plan_limit * 100, 1)
    else:
        usage_percent = 0.0

    return {
        "sessions_used": sessions_used,
        "max_usage_percent": max_usage_pct,
        "plan_limit": plan_limit,
        "window_start_ts": window_start,
        "window_remaining_hours": round(remaining_hours, 1),
        "usage_percent": usage_percent,
        "last_run_ts": last_run,
    }


@router.get("/token-usage")
async def get_token_usage(db: AsyncSession = Depends(get_db)):
    """Get token consumption data.

    Uses the new simplified reserve_percent field. The old per-day/per-month
    hard limits have been removed in favor of plan-aware usage tracking.
    """
    config = await asyncio.to_thread(_read_config_json)
    limits = config.get("limits", {})

    # New simplified fields (with backward-compat fallback)
    max_usage_pct = limits.get(
        "max_usage_percent",
        limits.get("max_session_percent", _NEW_LIMIT_DEFAULTS["max_usage_percent"]),
    )
    reserve_pct = limits.get(
        "reserve_percent",
        limits.get("token_reserve_percent", _NEW_LIMIT_DEFAULTS["reserve_percent"]),
    )

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

    return {
        "daily": {
            "tokens_input": daily_input,
            "tokens_output": daily_output,
            "tokens_total": daily_total,
        },
        "monthly": {
            "tokens_total": monthly_total,
        },
        "max_usage_percent": max_usage_pct,
        "reserve_percent": reserve_pct,
    }


@router.post("/test-notification")
async def test_notification():
    """Send a test webhook notification to verify configuration."""
    result = await send_test_notification()
    if result.get("success"):
        return result
    raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
