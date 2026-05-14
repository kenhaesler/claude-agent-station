"""Dialect-agnostic decoders for columns of type ``JsonType`` (#393).

Postgres JSONB returns ``dict`` directly; SQLite returns ``str``. Callers
should funnel through ``decode_event_data`` rather than calling ``json.loads``
directly.
"""
from __future__ import annotations

import json
from typing import Any


def decode_event_data(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            return None
    return None
