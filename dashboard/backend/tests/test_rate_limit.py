"""Tests for agent.rate_limit (issue #383 bash port)."""
from __future__ import annotations

import json
import time
import pytest


def test_fresh_state_not_tripped(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    assert rate_limit.is_tripped() is False


def test_per_day_cap_trips(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    now = time.time()
    sidecar.write_text(json.dumps({"sessions": [now - 60 for _ in range(100)]}))
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    monkeypatch.setattr(rate_limit, "_PER_DAY_CAP", 50)
    assert rate_limit.is_tripped() is True


def test_per_hour_cap_trips(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    now = time.time()
    sidecar.write_text(json.dumps({"sessions": [now - 60 for _ in range(10)]}))
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    monkeypatch.setattr(rate_limit, "_PER_HOUR_CAP", 5)
    monkeypatch.setattr(rate_limit, "_PER_DAY_CAP", 9999)
    assert rate_limit.is_tripped() is True


def test_malformed_sidecar_not_tripped(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    sidecar.write_text("{not json")
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    # Malformed sidecar should fail open (don't block the run on a bad file).
    assert rate_limit.is_tripped() is False


def test_record_session_appends(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))

    rate_limit.record_session()
    data = json.loads(sidecar.read_text())
    assert len(data["sessions"]) == 1

    rate_limit.record_session()
    data = json.loads(sidecar.read_text())
    assert len(data["sessions"]) == 2
