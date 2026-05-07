"""Unit tests for the GitHub PAT service module."""

from __future__ import annotations

import os

import pytest


def test_pat_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("STATION_GITHUB_PAT_PATH", raising=False)
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    expected = os.path.expanduser("~/.claude-agent-station/github_pat.json")
    assert str(github_pat.PAT_PATH) == expected


def test_pat_path_honors_env(monkeypatch, tmp_path):
    custom = tmp_path / "pat.json"
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(custom))
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    assert github_pat.PAT_PATH == custom


def test_read_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    assert github_pat.read_pat() is None


def test_write_then_read_round_trips_with_mode_0600(monkeypatch, tmp_path):
    target = tmp_path / "pat.json"
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(target))
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    github_pat.write_pat("ghp_secret_value")

    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert github_pat.read_pat() == "ghp_secret_value"


def test_delete_removes_file_and_is_idempotent(monkeypatch, tmp_path):
    target = tmp_path / "pat.json"
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(target))
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    github_pat.write_pat("ghp_x")
    assert target.exists()
    github_pat.delete_pat()
    assert not target.exists()
    # Idempotent
    github_pat.delete_pat()


def test_read_returns_none_on_corrupt_file(monkeypatch, tmp_path):
    target = tmp_path / "pat.json"
    target.write_text("not valid json")
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(target))
    import importlib

    from app.services import github_pat
    importlib.reload(github_pat)

    assert github_pat.read_pat() is None
