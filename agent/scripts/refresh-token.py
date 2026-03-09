#!/usr/bin/env python3
"""Refresh Claude OAuth token using the refresh_token grant.

Reads credentials from ~/.claude/.credentials.json, calls the Anthropic
OAuth token endpoint, and writes the updated credentials back.

Exit codes:
  0 - Token refreshed successfully (or still valid with enough time remaining)
  1 - Refresh failed
  2 - No credentials file found
  3 - No refresh token available
"""

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
# Refresh if less than this many seconds remain (default: 1 hour)
REFRESH_THRESHOLD_SECONDS = int(os.environ.get("REFRESH_THRESHOLD", "3600"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("refresh-token")


def get_creds_path() -> Path:
    """Find credentials file."""
    # Check explicit env var first
    if env_path := os.environ.get("CLAUDE_CREDENTIALS_PATH"):
        return Path(env_path)
    return Path.home() / ".claude" / ".credentials.json"


def read_credentials(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def write_credentials(path: Path, data: dict) -> None:
    """Atomic write: write to temp file then rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def token_needs_refresh(oauth: dict) -> bool:
    """Check if the access token needs refreshing."""
    expires_at = oauth.get("expiresAt", 0)
    if not expires_at:
        return True
    # expiresAt is in epoch milliseconds
    remaining = (expires_at / 1000) - time.time()
    log.info(f"Token expires in {remaining:.0f}s ({remaining/3600:.1f}h)")
    return remaining < REFRESH_THRESHOLD_SECONDS


def refresh_token(refresh_token_value: str) -> dict:
    """Call the OAuth token endpoint with refresh_token grant."""
    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": CLIENT_ID,
    }).encode("utf-8")

    req = Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error(f"Token refresh failed: HTTP {e.code}: {body}")
        raise
    except URLError as e:
        log.error(f"Token refresh failed: {e.reason}")
        raise


def main() -> int:
    creds_path = get_creds_path()

    if not creds_path.exists():
        log.error(f"Credentials file not found: {creds_path}")
        return 2

    creds = read_credentials(creds_path)
    oauth = creds.get("claudeAiOauth", {})

    if not oauth.get("refreshToken"):
        log.error("No refresh token in credentials")
        return 3

    if not token_needs_refresh(oauth):
        log.info("Token is still valid, no refresh needed")
        return 0

    log.info("Refreshing OAuth token...")
    try:
        result = refresh_token(oauth["refreshToken"])
    except Exception as e:
        log.error(f"Failed to refresh token: {e}")
        return 1

    # Update credentials with new values
    if "access_token" in result:
        oauth["accessToken"] = result["access_token"]
    if "refresh_token" in result:
        oauth["refreshToken"] = result["refresh_token"]
    if "expires_in" in result:
        # Convert expires_in (seconds) to expiresAt (epoch ms)
        oauth["expiresAt"] = int((time.time() + result["expires_in"]) * 1000)
    elif "expires_at" in result:
        oauth["expiresAt"] = result["expires_at"]

    creds["claudeAiOauth"] = oauth
    write_credentials(creds_path, creds)

    remaining = (oauth.get("expiresAt", 0) / 1000) - time.time()
    log.info(f"Token refreshed successfully. New expiry in {remaining/3600:.1f}h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
