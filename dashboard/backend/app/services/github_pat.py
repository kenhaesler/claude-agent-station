"""GitHub Personal Access Token storage.

Sibling of ``app.services.github_app``: stores a user-supplied PAT as an
alternative auth path when the GitHub App manifest flow isn't usable
(e.g. dashboard reachable only on localhost or a private VM, where
GitHub rejects the manifest's hook URL validation).

Same on-disk discipline as the App credentials: JSON file, atomic write,
mode 0600. Path is env-driven via ``STATION_GITHUB_PAT_PATH``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PAT_PATH = Path(
    os.environ.get(
        "STATION_GITHUB_PAT_PATH",
        str(Path.home() / ".claude-agent-station" / "github_pat.json"),
    )
)


def read_pat() -> str | None:
    """Return the persisted PAT, or None if not configured or unreadable."""
    if not PAT_PATH.exists():
        return None
    try:
        with PAT_PATH.open() as f:
            return json.load(f).get("token")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read GitHub PAT file: %s", e)
        return None


def write_pat(token: str) -> None:
    """Atomically write the PAT JSON, chmod 0600 from creation."""
    PAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PAT_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token}, f)
    tmp.replace(PAT_PATH)


def delete_pat() -> None:
    """Remove the PAT file. Idempotent."""
    try:
        PAT_PATH.unlink()
    except FileNotFoundError:
        pass
