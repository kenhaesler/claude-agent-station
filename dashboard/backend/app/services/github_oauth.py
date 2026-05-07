"""GitHub OAuth App login.

OAuth 2.0 web flow. The operator creates an OAuth App in their GitHub
settings (one-time, ~60 seconds), enters its client_id and client_secret
here, then signs in via the standard authorize/callback dance. Access
tokens for OAuth Apps are long-lived (no rotation like the GitHub App's
installation tokens), so we just persist the token alongside the config.

Unlike the GitHub App manifest flow, OAuth Apps explicitly support
``http://localhost`` callback URLs, which makes this the cleanest path
for dev VMs where GitHub rejects the App's hook-URL validation.

Storage at ``STATION_GITHUB_OAUTH_PATH`` (env-driven). chmod 0600 atomic
write. Same pattern as :mod:`app.services.github_app` and
:mod:`app.services.github_pat`.

On-disk shape:
    {
        "client_id": "Iv1.…",
        "client_secret": "…",
        "access_token": "gho_…" | null,
        "username": "octocat" | null,
        "scope": "repo workflow" | null,
    }
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

OAUTH_PATH = Path(
    os.environ.get(
        "STATION_GITHUB_OAUTH_PATH",
        str(Path.home() / ".claude-agent-station" / "github_oauth.json"),
    )
)


def read_oauth() -> dict | None:
    """Return the persisted OAuth state, or None if not configured."""
    if not OAUTH_PATH.exists():
        return None
    try:
        with OAUTH_PATH.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read GitHub OAuth file: %s", e)
        return None


def write_oauth(data: dict) -> None:
    """Atomically write the OAuth JSON, chmod 0600 from creation."""
    OAUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OAUTH_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(OAUTH_PATH)


def delete_oauth() -> None:
    """Remove the OAuth file entirely (config + token). Idempotent."""
    try:
        OAUTH_PATH.unlink()
    except FileNotFoundError:
        pass


def clear_token() -> None:
    """Remove the session fields (access_token, username, scope) but keep the
    OAuth App credentials so the user can sign in again without re-pasting
    their client_id / client_secret. No-op when no config is persisted.
    """
    state = read_oauth()
    if not state:
        return
    state["access_token"] = None
    state["username"] = None
    state["scope"] = None
    write_oauth(state)
