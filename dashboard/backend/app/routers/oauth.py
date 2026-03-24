"""OAuth PKCE login flow for claude-agent user."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers"
CREDS_PATH = Path(settings.credentials_path)

# In-memory store for pending PKCE flows: {state: (code_verifier, expires_at)}
_pending: dict[str, tuple[str, float]] = {}
STATE_TTL_SECONDS = 600  # 10 minutes
MAX_PENDING = 100


def _cleanup_expired_states() -> None:
    """Remove expired state entries from the pending store (lazy eviction)."""
    now = time.time()
    expired = [s for s, (_, exp) in _pending.items() if now > exp]
    for s in expired:
        del _pending[s]


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


class OAuthCallbackResponse(BaseModel):
    success: bool
    error: str | None = None


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _write_credentials(path: Path, data: dict) -> None:
    """Atomic write: write to temp file then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        # Ensure the claude-agent user can read the credentials
        import shutil
        with contextlib.suppress(LookupError, OSError):
            shutil.chown(path, user="claude-agent", group="claude-agent")
        os.chmod(path, 0o600)
    except Exception:
        os.unlink(tmp)
        raise


@router.post("/start", response_model=OAuthStartResponse)
async def start_oauth():
    """Generate PKCE challenge and return authorization URL."""
    _cleanup_expired_states()
    if len(_pending) >= MAX_PENDING:
        raise HTTPException(status_code=429, detail="Too many pending OAuth flows. Try again later.")
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce()
    _pending[state] = (code_verifier, time.time() + STATE_TTL_SECONDS)

    params = urlencode({
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"

    logger.info("OAuth flow started (state=%s...)", state[:8])
    return OAuthStartResponse(auth_url=auth_url, state=state)


def _clean_code(raw: str) -> str:
    """Extract authorization code from various user input formats.

    The callback page at platform.claude.com shows the code as 'code#state'
    combined with a '#' delimiter. Handle that and other formats.
    """
    raw = raw.strip()
    # If user pasted a full URL, extract the code param
    if raw.startswith("http"):
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        if "code" in qs:
            raw = qs["code"][0]
    # If user pasted code=XXXX or code=XXXX&state=...
    elif raw.startswith("code="):
        raw = raw.split("=", 1)[1].split("&")[0]
    # The callback page shows "code#state" — extract just the code part
    if "#" in raw:
        raw = raw.split("#")[0]
    # URL-decode in case of percent-encoded characters
    return unquote(raw)


@router.post("/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(req: OAuthCallbackRequest):
    """Exchange authorization code for tokens and write credentials."""
    _cleanup_expired_states()
    entry = _pending.pop(req.state, None)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    code_verifier, expires_at = entry
    if time.time() > expires_at:
        raise HTTPException(
            status_code=400,
            detail="OAuth state has expired. Please restart the login flow.",
        )

    code = _clean_code(req.code)
    logger.info(
        "Token exchange: raw_length=%d clean_length=%d raw_first20=%r clean_first20=%r",
        len(req.code), len(code), req.code[:20], code[:20],
    )

    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
        "state": req.state,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(
                TOKEN_URL,
                json=token_payload,
                headers={"User-Agent": "claude-agent-station/1.0"},
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        logger.error("Token exchange failed: HTTP %d: %s", e.response.status_code, body)
        return OAuthCallbackResponse(success=False, error=f"Token exchange failed: {body}")
    except httpx.RequestError as e:
        logger.error("Token exchange failed: %s", e)
        return OAuthCallbackResponse(success=False, error=f"Token exchange failed: {e}")

    # Build credentials in the expected format
    expires_at_ms = int((time.time() + result.get("expires_in", 3600)) * 1000)
    creds = {
        "claudeAiOauth": {
            "accessToken": result["access_token"],
            "refreshToken": result.get("refresh_token", ""),
            "expiresAt": expires_at_ms,
            "scopes": SCOPES.split(" "),
        }
    }

    try:
        _write_credentials(CREDS_PATH, creds)
        logger.info("Credentials written to %s", CREDS_PATH)
    except Exception as e:
        logger.error("Failed to write credentials: %s", e)
        return OAuthCallbackResponse(success=False, error=f"Failed to write credentials: {e}")

    return OAuthCallbackResponse(success=True)


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

REFRESH_THRESHOLD_SECONDS = 600  # 10 minutes


class OAuthRefreshResponse(BaseModel):
    refreshed: bool
    error: str | None = None
    expires_at: str | None = None


@router.post("/refresh", response_model=OAuthRefreshResponse)
async def refresh_oauth_token():
    """Refresh the OAuth access token using the stored refresh token.

    Reads the current credentials, checks whether the token is within
    REFRESH_THRESHOLD_SECONDS of expiry, and if so performs a refresh_token
    grant against the Anthropic OAuth endpoint.  The updated credentials are
    written back atomically.

    If the token is still valid with enough remaining time, this is a no-op
    that returns refreshed=False (not an error).
    """
    if not CREDS_PATH.exists():
        raise HTTPException(status_code=404, detail="Credentials file not found")

    try:
        with open(CREDS_PATH) as f:
            creds = json.load(f)
    except Exception as e:
        logger.error("Failed to read credentials: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read credentials: {e}")

    oauth = creds.get("claudeAiOauth", {})
    refresh_token_value = oauth.get("refreshToken")
    if not refresh_token_value:
        raise HTTPException(status_code=400, detail="No refresh token available in credentials")

    # Check if refresh is actually needed
    expires_at_ms = oauth.get("expiresAt", 0)
    remaining = (expires_at_ms / 1000) - time.time() if expires_at_ms else 0
    if remaining > REFRESH_THRESHOLD_SECONDS:
        expires_dt = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc)
        return OAuthRefreshResponse(
            refreshed=False,
            error=None,
            expires_at=expires_dt.isoformat(),
        )

    # Perform the refresh
    token_payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": CLIENT_ID,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(
                TOKEN_URL,
                json=token_payload,
                headers={"User-Agent": "claude-agent-station/1.0"},
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        logger.error("Token refresh failed: HTTP %d: %s", e.response.status_code, body)
        return OAuthRefreshResponse(refreshed=False, error=f"Token refresh failed: {body}")
    except httpx.RequestError as e:
        logger.error("Token refresh failed: %s", e)
        return OAuthRefreshResponse(refreshed=False, error=f"Token refresh failed: {e}")

    # Update credentials with new values
    if "access_token" in result:
        oauth["accessToken"] = result["access_token"]
    if "refresh_token" in result:
        oauth["refreshToken"] = result["refresh_token"]
    if "expires_in" in result:
        oauth["expiresAt"] = int((time.time() + result["expires_in"]) * 1000)
    elif "expires_at" in result:
        oauth["expiresAt"] = result["expires_at"]

    creds["claudeAiOauth"] = oauth

    try:
        _write_credentials(CREDS_PATH, creds)
        logger.info("Token refreshed, credentials written to %s", CREDS_PATH)
    except Exception as e:
        logger.error("Failed to write refreshed credentials: %s", e)
        return OAuthRefreshResponse(refreshed=False, error=f"Failed to write credentials: {e}")

    new_expires_ms = oauth.get("expiresAt", 0)
    expires_dt = datetime.fromtimestamp(new_expires_ms / 1000, tz=timezone.utc)
    return OAuthRefreshResponse(refreshed=True, expires_at=expires_dt.isoformat())
