"""stale_run_reaper pub/sub integration (#393)."""
from __future__ import annotations

import pytest

from app.services import stale_run_reaper


def test_reaper_default_tick_is_15s_on_sqlite(monkeypatch):
    monkeypatch.delenv("STATION_DB_URL", raising=False)
    assert stale_run_reaper.tick_interval_seconds() == 15


def test_reaper_default_tick_is_60s_on_postgres(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "postgresql+asyncpg://x:y@z/db")
    assert stale_run_reaper.tick_interval_seconds() == 60
