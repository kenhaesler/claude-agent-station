"""Session rate limiting via a JSON sidecar (issue #383 bash port).

Mirrors the bash check_rate_limit / record_session pair so old rate-limit
state survives the migration without a data conversion step.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


_SIDECAR_PATH = os.environ.get(
    "STATION_RATE_LIMIT_PATH",
    "/var/lib/claude-agent-station/rate-limit.json",
)

# Defaults can be overridden by tests or by config in a follow-up.
_PER_DAY_CAP = int(os.environ.get("STATION_RATE_LIMIT_PER_DAY", "200"))
_PER_HOUR_CAP = int(os.environ.get("STATION_RATE_LIMIT_PER_HOUR", "50"))


def _read_sessions() -> list[float]:
    try:
        data = json.loads(Path(_SIDECAR_PATH).read_text())
        sessions = data.get("sessions") or []
        return [float(s) for s in sessions]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("rate_limit: sidecar unreadable (%s); failing open", exc)
        return []


def _write_sessions(sessions: list[float]) -> None:
    p = Path(_SIDECAR_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sessions": sessions}))


def is_tripped() -> bool:
    """True iff the current rate exceeds either cap."""
    now = time.time()
    sessions = _read_sessions()
    last_day = [s for s in sessions if now - s < 86400]
    last_hour = [s for s in sessions if now - s < 3600]
    if len(last_day) >= _PER_DAY_CAP:
        logger.warning("rate_limit: per-day cap reached (%d/%d)", len(last_day), _PER_DAY_CAP)
        return True
    if len(last_hour) >= _PER_HOUR_CAP:
        logger.warning("rate_limit: per-hour cap reached (%d/%d)", len(last_hour), _PER_HOUR_CAP)
        return True
    return False


def record_session() -> None:
    """Append `now` to the sidecar."""
    sessions = _read_sessions()
    sessions.append(time.time())
    # Trim sessions older than 24h so the file doesn't grow unbounded.
    cutoff = time.time() - 86400
    sessions = [s for s in sessions if s >= cutoff]
    _write_sessions(sessions)
