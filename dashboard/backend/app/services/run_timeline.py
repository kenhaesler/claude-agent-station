"""Run timeline service — merges five source tables into one ordered stream.

Owns the heap-merge, the per-source projection, and cursor-based pagination.
The HTTP layer (``routers/runs.py``) only orchestrates the FastAPI shape.

See spec: docs/superpowers/specs/2026-05-14-issue-387-run-timeline-api.md
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TimelineCursor:
    t: datetime
    source: str
    source_id: str

    def encode(self) -> str:
        payload = {
            "t": self.t.isoformat(),
            "s": self.source,
            "i": self.source_id,
        }
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    @classmethod
    def decode(cls, encoded: str) -> "TimelineCursor":
        try:
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
            return cls(
                t=datetime.fromisoformat(data["t"]),
                source=data["s"],
                source_id=str(data["i"]),
            )
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid cursor: {exc}") from exc
