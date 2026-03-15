"""GitHub OAuth login flow for connecting a GitHub account to the station."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth/github", tags=["github-oauth"])

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"
SCOPES = "repo read:org read:user"

# Default token storage path (alongside Claude credentials)
GITHUB_TOKEN_PATH = Path.home() / ".claude-agent-station" / "github_token"

# In-memory store for pending OAuth flows: {state: expires_at}
_pending: dict[str, float] = {}
STATE_TTL_SECONDS = 600  # 10 minutes


def _cleanup_expired_states() -> None:
    """Remove expired state entries from the pending store (lazy eviction)."""
    now = time.time()
    expired = [s for s, exp in _pending.items() if now > exp]
    for s in expired:
        del _pending[s]


class GitHubOAuthStartResponse(BaseModel):
    auth_url: str
    state: str


class GitHubOAuthCallbackRequest(BaseModel):
    code: str
    state: str


class GitHubOAuthCallbackResponse(BaseModel):
    success: bool
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
        with contextlib.suppress(LookupError, OSError):
            shutil.chown(path, user="claude-agent", group="claude-agent")
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


@router.get("/start", response_model=GitHubOAuthStartResponse)
async def start_github_oauth():
    """Generate state parameter and return GitHub authorization URL."""
    client_id = settings.github_client_id
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth not configured. Set STATION_GITHUB_CLIENT_ID environment variable.",
        )

    _cleanup_expired_states()
    state = secrets.token_urlsafe(32)
    _pending[state] = time.time() + STATE_TTL_SECONDS

    redirect_uri = settings.github_oauth_redirect_uri
    params_dict: dict[str, str] = {
        "client_id": client_id,
        "scope": SCOPES,
        "state": state,
    }
    if redirect_uri:
        params_dict["redirect_uri"] = redirect_uri

    from urllib.parse import urlencode
    auth_url = f"{AUTHORIZE_URL}?{urlencode(params_dict)}"

    logger.info("GitHub OAuth flow started (state=%s...)", state[:8])
    return GitHubOAuthStartResponse(auth_url=auth_url, state=state)


@router.post("/callback", response_model=GitHubOAuthCallbackResponse)
async def github_oauth_callback(req: GitHubOAuthCallbackRequest):
    """Exchange authorization code for access token, fetch user info, store token."""
    expires_at = _pending.pop(req.state, None)
    if expires_at is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    if time.time() > expires_at:
        raise HTTPException(
            status_code=400,
            detail="OAuth state has expired. Please restart the login flow.",
        )

    client_id = settings.github_client_id
    client_secret = settings.github_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth not configured. Set STATION_GITHUB_CLIENT_ID and STATION_GITHUB_CLIENT_SECRET.",
        )

    # Exchange code for access token
    token_payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": req.code,
        "state": req.state,
    }
    redirect_uri = settings.github_oauth_redirect_uri
    if redirect_uri:
        token_payload["redirect_uri"] = redirect_uri

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(
                TOKEN_URL,
                json=token_payload,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "claude-agent-station/1.0",
                },
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        logger.error("GitHub token exchange failed: HTTP %d: %s", e.response.status_code, body)
        return GitHubOAuthCallbackResponse(success=False, error=f"Token exchange failed: {body}")
    except httpx.RequestError as e:
        logger.error("GitHub token exchange failed: %s", e)
        return GitHubOAuthCallbackResponse(success=False, error=f"Token exchange failed: {e}")

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        logger.error("GitHub OAuth error: %s", error_desc)
        return GitHubOAuthCallbackResponse(success=False, error=error_desc)

    access_token = result.get("access_token")
    if not access_token:
        return GitHubOAuthCallbackResponse(success=False, error="No access token in response")

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
        return GitHubOAuthCallbackResponse(success=False, error=f"Failed to store token: {e}")

    return GitHubOAuthCallbackResponse(success=True, username=username)


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
