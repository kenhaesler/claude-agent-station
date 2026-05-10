"""Storage for third-party provider API keys (OpenAI, Gemini, ...).

Sibling of ``app.services.github_pat`` — simple bring-your-own-key
persistence for non-Anthropic LLM providers used by specialised
teammate roles. No OAuth flow, no refresh dance, just a JSON file.

On-disk discipline mirrors the GitHub PAT path:
  - JSON file, atomic write, mode 0600
  - Path is env-driven via ``STATION_PROVIDER_KEYS_PATH``
  - Lives under the station-data volume in compose so keys survive
    container rebuilds.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Set of providers we recognise. The router validates against this so
# adding e.g. Mistral later is a one-line change here + a UI panel.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "gemini")


def _path() -> Path:
    """Resolve the storage path lazily so tests can monkeypatch the env var."""
    return Path(
        os.environ.get(
            "STATION_PROVIDER_KEYS_PATH",
            str(Path.home() / ".claude-agent-station" / "provider_keys.json"),
        )
    )


def _read_all() -> dict[str, dict]:
    """Load the full {provider: {key, last_updated}} map; {} on missing/corrupt."""
    p = _path()
    if not p.exists():
        return {}
    try:
        with p.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read provider keys file: %s", e)
        return {}


def _write_all(data: dict[str, dict]) -> None:
    """Atomic write with mode 0600 from creation."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    tmp.replace(p)


def mask_key(provider: str, key: str) -> str:
    """Return a masked representation safe to send back over HTTP.

    Provider-specific prefixes get more context preserved (so the user
    can sanity-check which key is wired) while still hiding the body.
    """
    if not key:
        return ""
    if provider == "openai" and key.startswith("sk-") and len(key) > 12:
        return f"{key[:6]}…{key[-4:]}"
    if provider == "gemini" and key.startswith("AIza") and len(key) > 12:
        return f"{key[:6]}…{key[-4:]}"
    if len(key) > 12:
        return f"{key[:4]}…{key[-4:]}"
    # Too short to safely reveal anything — fully redact.
    return "*" * len(key)


def read_key(provider: str) -> str | None:
    """Return the raw stored key for ``provider``, or None."""
    return _read_all().get(provider, {}).get("key") or None


def get_status(provider: str) -> dict:
    """Return ``{configured, masked_key, last_updated}`` for the UI."""
    entry = _read_all().get(provider) or {}
    key = entry.get("key")
    if not key:
        return {"configured": False, "masked_key": None, "last_updated": None}
    return {
        "configured": True,
        "masked_key": mask_key(provider, key),
        "last_updated": entry.get("last_updated"),
    }


def write_key(provider: str, key: str) -> dict:
    """Persist ``key`` for ``provider``; return its new public status."""
    data = _read_all()
    data[provider] = {
        "key": key,
        "last_updated": datetime.now(UTC).isoformat(),
    }
    _write_all(data)
    return get_status(provider)


def delete_key(provider: str) -> dict:
    """Remove the stored key for ``provider``. Idempotent."""
    data = _read_all()
    if provider in data:
        data.pop(provider, None)
        _write_all(data)
    return get_status(provider)
