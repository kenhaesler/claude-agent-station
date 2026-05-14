"""log_importer pub/sub integration (#393)."""
from __future__ import annotations

import pytest

from app.services import log_importer


def test_default_poll_interval_is_300_on_postgres(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "postgresql+asyncpg://x:y@z/db")
    assert log_importer.poll_interval_seconds() == 300


def test_default_poll_interval_is_30_on_sqlite(monkeypatch):
    monkeypatch.delenv("STATION_DB_URL", raising=False)
    monkeypatch.setenv("STATION_DB_PATH", "/tmp/x.db")
    assert log_importer.poll_interval_seconds() == 30
