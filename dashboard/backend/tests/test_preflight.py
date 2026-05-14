"""Tests for agent.preflight (issue #383 bash port)."""
from __future__ import annotations

import json
import pytest


def test_preflight_passes_on_clean_config(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
    }))
    monkeypatch.setenv("CLAUDE_OAUTH_TOKEN", "sk-test")
    # Pretend gh/git are present.
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: False)

    # Should not raise.
    run_preflight(str(cfg))


def test_preflight_raises_on_missing_config(tmp_path):
    from agent.preflight import run_preflight, PreflightError

    with pytest.raises(PreflightError, match="config"):
        run_preflight(str(tmp_path / "missing.json"))


def test_preflight_raises_on_missing_dependency(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: name != "gh")

    with pytest.raises(PreflightError, match="gh"):
        run_preflight(str(cfg))


def test_preflight_raises_on_oauth_refresh_failure(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._refresh_oauth_token", lambda: False)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: False)
    monkeypatch.delenv("CLAUDE_OAUTH_TOKEN", raising=False)

    with pytest.raises(PreflightError, match="OAuth"):
        run_preflight(str(cfg))


def test_preflight_raises_when_rate_limit_tripped(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setenv("CLAUDE_OAUTH_TOKEN", "sk-test")
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: True)

    with pytest.raises(PreflightError, match="rate limit"):
        run_preflight(str(cfg))
