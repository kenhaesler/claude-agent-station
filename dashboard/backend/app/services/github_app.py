"""GitHub App identity management.

Stores App credentials (id, private key, slug, etc.) in a JSON file with
mode 0600. Path is env-driven via ``STATION_GITHUB_APP_CREDENTIALS_PATH``;
the default lives alongside Claude credentials in the user's home dir,
which is right for systemd installs but gets wiped on every ``compose
up --build`` — compose.yml sets the env to the ``station-data`` named
volume.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = Path(
    os.environ.get(
        "STATION_GITHUB_APP_CREDENTIALS_PATH",
        str(Path.home() / ".claude-agent-station" / "github_app.json"),
    )
)


def read_credentials() -> dict | None:
    """Return the persisted App credentials dict, or None if not configured."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        with CREDENTIALS_PATH.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read GitHub App config file: %s", e)
        return None


def write_credentials(data: dict) -> None:
    """Atomically write the credentials JSON, chmod 0600."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CREDENTIALS_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CREDENTIALS_PATH)


def delete_credentials() -> None:
    """Remove the credentials file. Idempotent."""
    try:
        CREDENTIALS_PATH.unlink()
    except FileNotFoundError:
        pass


import time

import jwt as pyjwt

# GitHub allows JWT iat up to 60s in the past; subtract 60 from iat to absorb
# clock drift between the dashboard host and GitHub. Max exp is iat+600s.
_JWT_IAT_BUFFER_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540  # under the 600 ceiling, with safety margin


def make_jwt(app_id: int, private_key_pem: str) -> str:
    """Sign a short-lived RS256 JWT for App-level GitHub API calls.

    GitHub requires App JWTs to have ``iss`` set to the App's numeric id and
    a ``iat``/``exp`` window of at most 600 seconds. We use 540s and
    pre-date ``iat`` by 60s for clock skew.
    """
    now = int(time.time())
    payload = {
        "iat": now - _JWT_IAT_BUFFER_SECONDS,
        "exp": now - _JWT_IAT_BUFFER_SECONDS + _JWT_LIFETIME_SECONDS,
        # PyJWT >= 2.9 enforces RFC 7519: iss must be a string.
        # GitHub accepts "12345" the same as 12345.
        "iss": str(app_id),
    }
    return pyjwt.encode(payload, private_key_pem, algorithm="RS256")


from datetime import datetime, timezone

import httpx

# Refresh threshold: when an installation token has less than this many
# seconds left, treat it as expired and mint a new one.
_TOKEN_REFRESH_THRESHOLD_SECONDS = 300

# In-memory cache: {installation_id: (token, expires_at_epoch)}.
# Single-process state; the launcher and dashboard run independently and
# each maintains their own cache, which is fine because every miss just
# costs one extra GitHub round-trip.
_token_cache: dict[int, tuple[str, float]] = {}


def _parse_iso8601(s: str) -> float:
    """GitHub returns ``2026-05-06T23:00:00Z``; convert to epoch seconds."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


async def get_installation_token() -> str | None:
    """Return a fresh installation access token, minting one if needed.

    Returns None if the App is not yet configured (manifest not exchanged,
    or App not yet installed on any account). Callers should treat None
    as "GitHub integration is not set up; surface a clear UI message."
    """
    creds = read_credentials()
    if not creds:
        return None
    app_id = creds.get("app_id")
    pem = creds.get("pem")
    installation_id = creds.get("installation_id")
    if not (app_id and pem and installation_id):
        return None

    # Cache hit?
    cached = _token_cache.get(installation_id)
    if cached:
        token, expires_at = cached
        if expires_at - time.time() > _TOKEN_REFRESH_THRESHOLD_SECONDS:
            return token

    # Cache miss — mint a new token.
    jwt_token = make_jwt(app_id=app_id, private_key_pem=pem)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers)
    if resp.status_code != 201:
        logger.warning("GitHub minting request failed (http_status=%s)", resp.status_code)
        return None
    data = resp.json()
    token = data["token"]
    expires_at = _parse_iso8601(data["expires_at"])
    _token_cache[installation_id] = (token, expires_at)
    return token
