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
