from __future__ import annotations

"""System status, service control, and auth endpoints."""

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import StationControl
from app.schemas import GlobalPauseState
from app.services import service_control
from app.services.event_bus import publish
from app.services.systemd import ALLOWED_ACTIONS, get_system_resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

# Refresh the token if it expires within this many seconds
_AUTH_REFRESH_THRESHOLD = 3600  # 1 hour — matches oauth.REFRESH_THRESHOLD_SECONDS


@router.get("/status")
async def system_status():
    """Get system and service status."""
    svc = await service_control.get_agent_status()
    resources = await get_system_resources()
    return {
        "service": {
            "active": svc["service_active"],
        },
        "timer": {
            "active": svc["timer_active"],
            "next_trigger": svc.get("timer_next"),
        },
        "resources": resources,
    }


@router.post("/service/{action}")
async def service_action(action: str, unit: str = "claude-agent.service"):
    """Control the agent service or timer."""
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Action not allowed: {action}")

    result = await service_control.run_action(action, unit)
    if not result.get("success"):
        status = result.get("status_code") or 500
        if status < 400:
            status = 500
        raise HTTPException(status_code=status, detail=result.get("error") or "Failed")
    return {"action": action, "unit": unit, "result": result}


@router.get("/auth")
async def auth_status():
    """Check Claude CLI auth status by inspecting credentials file.

    Returns remaining seconds until expiry and whether auto-refresh is
    available (i.e. a refresh token exists in the credentials).

    Automatically triggers a token refresh when the token is expired or
    within the refresh threshold, so the dashboard never shows stale auth.
    """
    creds_path = settings.credentials_path
    if not os.path.exists(creds_path):
        return {"logged_in": False, "expired": True}

    try:
        with open(creds_path) as f:
            creds = json.load(f)

        # credentials may be nested under claudeAiOauth
        oauth = creds.get("claudeAiOauth", creds)
        expires_at = oauth.get("expiresAt")
        if not expires_at:
            return {"logged_in": True, "expired": False, "expires_at": None}

        # expiresAt is epoch milliseconds
        expires_dt = datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining_seconds = max(0, int((expires_dt - now).total_seconds()))
        has_refresh_token = bool(oauth.get("refreshToken"))

        # Auto-refresh if expired or near-expiry and a refresh token exists
        if remaining_seconds <= _AUTH_REFRESH_THRESHOLD and has_refresh_token:
            try:
                from app.routers.oauth import refresh_oauth_token

                result = await refresh_oauth_token()
                if result.refreshed and result.expires_at:
                    # Re-read the updated values
                    expires_dt = datetime.fromisoformat(result.expires_at)
                    now = datetime.now(timezone.utc)
                    remaining_seconds = max(0, int((expires_dt - now).total_seconds()))
                    logger.info("Refresh complete; new expiry: %s", result.expires_at)
                elif result.error:
                    logger.warning("Auto-refresh failed: %s", result.error)
            except Exception as e:
                logger.warning("Auto-refresh attempt failed: %s", e)

        expired = remaining_seconds == 0

        return {
            "logged_in": True,
            "expired": expired,
            "expires_at": expires_dt.isoformat(),
            "remaining_seconds": remaining_seconds,
            "auto_refresh_available": has_refresh_token,
        }
    except Exception as e:
        return {"logged_in": False, "expired": True, "error": str(e)}


# --- Mission Control: global pause kill-switch (Phase A) -------------------


async def _get_or_create_station_control(db: AsyncSession) -> StationControl:
    row = await db.get(StationControl, 1)
    if row is None:
        row = StationControl(id=1, global_pause=False)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("/pause", response_model=GlobalPauseState)
async def get_global_pause(db: AsyncSession = Depends(get_db)):
    row = await _get_or_create_station_control(db)
    return GlobalPauseState(
        global_pause=bool(row.global_pause),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.post("/pause", response_model=GlobalPauseState)
async def set_global_pause(db: AsyncSession = Depends(get_db)):
    """Set the global_pause flag so every tool call on every run defers to
    the permission tray. Overrides autonomy level until resumed."""
    row = await _get_or_create_station_control(db)
    row.global_pause = True
    row.updated_by = "api"
    await db.commit()
    await db.refresh(row)
    await publish({"type": "global_pause_set", "data": {"by": "api"}})
    return GlobalPauseState(
        global_pause=True,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.post("/resume", response_model=GlobalPauseState)
async def clear_global_pause(db: AsyncSession = Depends(get_db)):
    """Clear the global_pause flag; agents return to their per-run autonomy."""
    row = await _get_or_create_station_control(db)
    row.global_pause = False
    row.updated_by = "api"
    await db.commit()
    await db.refresh(row)
    await publish({"type": "global_pause_cleared", "data": {"by": "api"}})
    return GlobalPauseState(
        global_pause=False,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )
