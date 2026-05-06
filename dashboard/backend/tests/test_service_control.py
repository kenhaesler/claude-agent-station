"""Tests for the deploy-mode-aware service control facade."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mode_default_is_systemd(monkeypatch):
    monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)
    from app.services import service_control
    assert service_control._mode() == "systemd"


@pytest.mark.asyncio
async def test_mode_reads_env_lowercase(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "COMPOSE")
    from app.services import service_control
    assert service_control._mode() == "compose"
