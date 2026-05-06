"""Unit tests for the GitHub App service module."""

from __future__ import annotations

import json
import os

import pytest


def test_credentials_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("STATION_GITHUB_APP_CREDENTIALS_PATH", raising=False)
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    expected = os.path.expanduser("~/.claude-agent-station/github_app.json")
    assert str(github_app.CREDENTIALS_PATH) == expected


def test_credentials_path_honors_env(monkeypatch, tmp_path):
    custom = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(custom))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    assert github_app.CREDENTIALS_PATH == custom


def test_read_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    assert github_app.read_credentials() is None


def test_write_then_read_round_trips(monkeypatch, tmp_path):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(target))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    payload = {"app_id": 42, "slug": "test-app", "pem": "PEM-DATA"}
    github_app.write_credentials(payload)

    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert github_app.read_credentials() == payload


def test_delete_removes_file(monkeypatch, tmp_path):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(target))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    github_app.write_credentials({"app_id": 1})
    assert target.exists()
    github_app.delete_credentials()
    assert not target.exists()
    # Idempotent
    github_app.delete_credentials()
