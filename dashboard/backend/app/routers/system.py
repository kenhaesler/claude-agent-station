"""System status, service control, and auth endpoints."""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.systemd import (
    get_service_status,
    get_system_resources,
    systemctl,
    ALLOWED_ACTIONS,
)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
async def system_status():
    """Get system and service status."""
    svc = await get_service_status()
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

    result = await systemctl(action, unit)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or result.get("stderr", "Command failed"),
        )
    return {"action": action, "unit": unit, "result": result}


@router.get("/auth")
async def auth_status():
    """Check Claude CLI auth status by inspecting credentials file."""
    creds_path = "/home/claude-agent/.claude/.credentials.json"
    if not os.path.exists(creds_path):
        return {"logged_in": False, "expired": True}

    try:
        with open(creds_path, "r") as f:
            creds = json.load(f)

        # credentials may be nested under claudeAiOauth
        oauth = creds.get("claudeAiOauth", creds)
        expires_at = oauth.get("expiresAt")
        if not expires_at:
            return {"logged_in": True, "expired": False, "expires_at": None}

        # expiresAt is epoch milliseconds
        expires_dt = datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc)
        expired = datetime.now(timezone.utc) > expires_dt

        return {
            "logged_in": True,
            "expired": expired,
            "expires_at": expires_dt.isoformat() + "Z",
        }
    except Exception as e:
        return {"logged_in": False, "expired": True, "error": str(e)}
