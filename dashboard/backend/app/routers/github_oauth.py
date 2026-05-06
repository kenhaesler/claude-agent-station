"""GitHub OAuth Device Authorization Flow for connecting a GitHub account."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth/github", tags=["github-oauth"])

DEVICE_CODE_URL = "https://github.com/login/device/code"
DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"
GITHUB_CLIENT_ID = "Ov23liUWRzu5iRGDS1kE"
SCOPES = "repo read:org read:user workflow"

# Default token storage path (alongside Claude credentials)
GITHUB_TOKEN_PATH = Path.home() / ".claude-agent-station" / "github_token"


@dataclass
class _DeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float
    last_poll: float = 0.0


_device_flows: dict[str, _DeviceFlow] = {}


def _cleanup_expired_flows() -> None:
    """Remove expired device flow entries (lazy eviction)."""
    now = time.time()
    expired = [fid for fid, flow in _device_flows.items() if now > flow.expires_at]
    for fid in expired:
        del _device_flows[fid]


class GitHubDeviceStartResponse(BaseModel):
    flow_id: str
    user_code: str
    verification_uri: str
    expires_in: int


class GitHubDevicePollRequest(BaseModel):
    flow_id: str


class GitHubDevicePollResponse(BaseModel):
    status: str  # "pending" | "complete" | "expired" | "error"
    username: str | None = None
    error: str | None = None


class GitHubOAuthStatusResponse(BaseModel):
    connected: bool
    username: str | None = None
    scopes: list[str] | None = None
    error: str | None = None


def _write_token(path: Path, data: dict) -> None:
    """Atomic write with chmod 0600, matching Claude credentials pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        # Ensure the claude-agent user can read the token
        import shutil
        service_user = os.environ.get("STATION_SERVICE_USER", "claude-agent")
        with contextlib.suppress(LookupError, OSError):
            shutil.chown(path, user=service_user, group=service_user)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_token(path: Path) -> dict | None:
    """Read stored GitHub token data, or None if not present."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read GitHub token from %s: %s", path, e)
        return None


def _delete_token(path: Path) -> None:
    """Delete stored GitHub token file."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


@router.post("/device/start", response_model=GitHubDeviceStartResponse)
async def start_device_flow():
    """Start a GitHub Device Authorization Flow."""
    _cleanup_expired_flows()

    async with httpx.AsyncClient(timeout=15.0) as http_client:
        response = await http_client.post(
            DEVICE_CODE_URL,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "scope": SCOPES,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "claude-agent-station/1.0",
            },
        )
        response.raise_for_status()
        result = response.json()

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        logger.error("GitHub device code request failed: %s", error_desc)
        raise HTTPException(status_code=502, detail=f"GitHub error: {error_desc}")

    device_code = result["device_code"]
    user_code = result["user_code"]
    verification_uri = result["verification_uri"]
    expires_in = int(result.get("expires_in", 900))
    interval = int(result.get("interval", 5))

    flow_id = secrets.token_urlsafe(32)
    _device_flows[flow_id] = _DeviceFlow(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        interval=interval,
        expires_at=time.time() + expires_in,
    )

    logger.info("GitHub device flow started (flow_id=%s..., user_code=%s)", flow_id[:8], user_code)
    return GitHubDeviceStartResponse(
        flow_id=flow_id,
        user_code=user_code,
        verification_uri=verification_uri,
        expires_in=expires_in,
    )


@router.post("/device/poll", response_model=GitHubDevicePollResponse)
async def poll_device_flow(req: GitHubDevicePollRequest):
    """Poll for device flow authorization status."""
    flow = _device_flows.get(req.flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Unknown or expired flow")

    now = time.time()

    # Check expiry
    if now > flow.expires_at:
        del _device_flows[req.flow_id]
        return GitHubDevicePollResponse(status="expired", error="Authorization expired. Try again.")

    # Enforce minimum poll interval
    if now - flow.last_poll < flow.interval:
        return GitHubDevicePollResponse(status="pending")

    flow.last_poll = now

    # Poll GitHub for token
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            response = await http_client.post(
                DEVICE_TOKEN_URL,
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "device_code": flow.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "claude-agent-station/1.0",
                },
            )
            response.raise_for_status()
            result = response.json()
    except httpx.RequestError as e:
        logger.warning("GitHub device poll network error: %s", e)
        return GitHubDevicePollResponse(status="pending")

    # Handle GitHub response
    if "error" in result:
        error = result["error"]
        if error == "authorization_pending":
            return GitHubDevicePollResponse(status="pending")
        elif error == "slow_down":
            flow.interval += 5
            return GitHubDevicePollResponse(status="pending")
        elif error == "expired_token":
            del _device_flows[req.flow_id]
            return GitHubDevicePollResponse(status="expired", error="Authorization expired. Try again.")
        elif error == "access_denied":
            del _device_flows[req.flow_id]
            return GitHubDevicePollResponse(status="error", error="Access denied by user.")
        else:
            error_desc = result.get("error_description", error)
            del _device_flows[req.flow_id]
            return GitHubDevicePollResponse(status="error", error=error_desc)

    access_token = result.get("access_token")
    if not access_token:
        return GitHubDevicePollResponse(status="error", error="No access token in response")

    token_type = result.get("token_type", "bearer")
    scope = result.get("scope", "")

    # Fetch GitHub user info
    username = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            user_response = await http_client.get(
                USER_API_URL,
                headers={
                    "Authorization": f"{token_type} {access_token}",
                    "Accept": "application/json",
                    "User-Agent": "claude-agent-station/1.0",
                },
            )
            user_response.raise_for_status()
            user_data = user_response.json()
            username = user_data.get("login")
    except Exception as e:
        logger.warning("Failed to fetch GitHub user info: %s", e)

    # Store token securely
    token_data = {
        "access_token": access_token,
        "token_type": token_type,
        "scope": scope,
        "username": username,
        "connected_at": int(time.time()),
    }

    try:
        _write_token(GITHUB_TOKEN_PATH, token_data)
        logger.info("GitHub token written to %s (user=%s)", GITHUB_TOKEN_PATH, username)
    except Exception as e:
        logger.error("Failed to write GitHub token: %s", e)
        del _device_flows[req.flow_id]
        return GitHubDevicePollResponse(status="error", error=f"Failed to store token: {e}")

    # Cleanup flow
    del _device_flows[req.flow_id]

    return GitHubDevicePollResponse(status="complete", username=username)


@router.get("/status", response_model=GitHubOAuthStatusResponse)
async def github_oauth_status():
    """Check if GitHub is connected and return username/scopes."""
    token_data = _read_token(GITHUB_TOKEN_PATH)
    if not token_data or not token_data.get("access_token"):
        return GitHubOAuthStatusResponse(connected=False)

    # Validate the token is still working by calling /user
    access_token = token_data["access_token"]
    token_type = token_data.get("token_type", "bearer")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(
                USER_API_URL,
                headers={
                    "Authorization": f"{token_type} {access_token}",
                    "Accept": "application/json",
                    "User-Agent": "claude-agent-station/1.0",
                },
            )
            if response.status_code == 401:
                return GitHubOAuthStatusResponse(
                    connected=False,
                    error="Token is no longer valid. Please reconnect.",
                )
            response.raise_for_status()
            user_data = response.json()
            username = user_data.get("login")
    except httpx.RequestError as e:
        # Network error — token might still be valid, return stored data
        logger.warning("Failed to validate GitHub token: %s", e)
        return GitHubOAuthStatusResponse(
            connected=True,
            username=token_data.get("username"),
            scopes=token_data.get("scope", "").split() if token_data.get("scope") else None,
            error="Could not verify token (network error)",
        )

    scope_str = token_data.get("scope", "")
    scopes = scope_str.split() if scope_str else None

    return GitHubOAuthStatusResponse(
        connected=True,
        username=username,
        scopes=scopes,
    )


@router.delete("")
async def disconnect_github():
    """Disconnect GitHub by deleting the stored token."""
    _delete_token(GITHUB_TOKEN_PATH)
    logger.info("GitHub token deleted from %s", GITHUB_TOKEN_PATH)
    return {"success": True, "message": "GitHub account disconnected"}
