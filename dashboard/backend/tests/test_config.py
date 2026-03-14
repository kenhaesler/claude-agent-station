"""Tests for the config router.

Covers:
- GET /api/config — returns config JSON (with limit migration)
- PUT /api/config — updates config (merges keys, normalizes limits, syncs DB)
- GET /api/config/db — returns DB config entries
- PUT /api/config/{key} — sets a config entry (valid and invalid keys)
- GET /api/config/usage — returns usage data
- GET /api/config/token-usage — returns token usage
"""

import json
import time
from datetime import timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import ConfigEntry, Project, Run


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /api/config — returns config JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_config(client):
    """GET /api/config should return the config JSON."""
    with patch("app.routers.config_router._read_config_json") as mock_read:
        mock_read.return_value = {
            "projects": [],
            "models": {"employee": "claude-sonnet"},
            "limits": {"max_usage_percent": 80, "reserve_percent": 20},
        }
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert data["limits"]["max_usage_percent"] == 80


@pytest.mark.asyncio
async def test_get_config_migrates_old_limits(client):
    """GET /api/config should migrate old limit field names to new ones."""
    with patch("app.routers.config_router._read_config_json") as mock_read:
        mock_read.return_value = {
            "projects": [],
            "limits": {
                "max_session_percent": 75,
                "token_reserve_percent": 15,
                "token_limit_daily": 100000,
            },
        }
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    limits = resp.json()["limits"]
    # Old fields should be removed, new fields derived
    assert "token_limit_daily" not in limits
    assert "max_session_percent" not in limits
    assert limits["max_usage_percent"] == 75
    assert limits["reserve_percent"] == 15


# ---------------------------------------------------------------------------
# PUT /api/config — updates config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_config(client):
    """PUT /api/config should merge config and return updated values."""
    current = {"projects": [], "models": {"employee": "old"}, "limits": {}}
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json={
                    "models": {"employee": "claude-sonnet-4"},
                })
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"]["employee"] == "claude-sonnet-4"
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_update_config_preserves_unset_keys(client):
    """PUT /api/config should not overwrite keys not sent by frontend."""
    current = {
        "projects": [],
        "models": {"employee": "old"},
        "limits": {"max_usage_percent": 80, "reserve_percent": 20},
        "schedule": {"interval": "4h"},
    }
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json={
                    "models": {"employee": "new-model"},
                })
    assert resp.status_code == 200
    written = mock_write.call_args[0][0]
    assert written["schedule"]["interval"] == "4h"  # preserved


# ---------------------------------------------------------------------------
# GET /api/config/db — returns DB config entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_config_db_empty(client):
    """GET /api/config/db should return empty dict when no entries exist."""
    resp = await client.get("/api/config/db")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_get_config_db_with_entries(client):
    """GET /api/config/db should return stored entries."""
    async with async_session() as session:
        entry = ConfigEntry(key="notifications", value='{"webhook_url": "http://x"}')
        session.add(entry)
        await session.commit()

    resp = await client.get("/api/config/db")
    assert resp.status_code == 200
    data = resp.json()
    assert "notifications" in data
    assert data["notifications"]["webhook_url"] == "http://x"


# ---------------------------------------------------------------------------
# PUT /api/config/{key} — sets a config entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_config_entry_valid_key(client):
    """PUT /api/config/{key} should store a valid config entry."""
    resp = await client.put("/api/config/notifications", json={"value": {"enabled": True}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "notifications"
    assert data["value"]["enabled"] is True


@pytest.mark.asyncio
async def test_set_config_entry_prompt_override(client):
    """PUT /api/config/{key} should allow prompt_override_ prefixed keys."""
    resp = await client.put(
        "/api/config/prompt_override_manager",
        json={"value": "custom prompt text"},
    )
    assert resp.status_code == 200
    assert resp.json()["key"] == "prompt_override_manager"


@pytest.mark.asyncio
async def test_set_config_entry_invalid_key(client):
    """PUT /api/config/{key} should reject unknown keys."""
    resp = await client.put("/api/config/not_allowed_key", json={"value": "x"})
    assert resp.status_code == 400
    assert "not in the allowed list" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_set_config_entry_upsert(client):
    """PUT /api/config/{key} should update existing entry."""
    await client.put("/api/config/schedule", json={"value": {"interval": "2h"}})
    resp = await client.put("/api/config/schedule", json={"value": {"interval": "6h"}})
    assert resp.status_code == 200
    assert resp.json()["value"]["interval"] == "6h"


# ---------------------------------------------------------------------------
# GET /api/config/usage — returns usage data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_usage_no_file(client, tmp_path):
    """GET /api/config/usage should return zeros when usage file doesn't exist."""
    # Point log_dir to a temp dir that has no usage-tracking.json
    with patch("app.routers.config_router._read_config_json", return_value={"limits": {}}):
        with patch("app.routers.config_router.settings") as mock_settings:
            mock_settings.log_dir = str(tmp_path)
            resp = await client.get("/api/config/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions_used"] == 0
    assert data["usage_percent"] == 0.0


@pytest.mark.asyncio
async def test_get_usage_with_file(client, tmp_path):
    """GET /api/config/usage should return data from usage tracking file."""
    usage_data = {
        "sessions_used": 5,
        "window_start": time.time() - 3600,
        "last_run": time.time() - 600,
        "plan_limit": 25,
    }
    usage_file = tmp_path / "usage-tracking.json"
    usage_file.write_text(json.dumps(usage_data))

    with patch("app.routers.config_router._read_config_json", return_value={"limits": {}}):
        with patch("app.routers.config_router.settings") as mock_settings:
            mock_settings.log_dir = str(tmp_path)
            resp = await client.get("/api/config/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions_used"] == 5
    assert data["plan_limit"] == 25
    assert data["usage_percent"] == 20.0  # 5/25 * 100


# ---------------------------------------------------------------------------
# GET /api/config/token-usage — returns token usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_token_usage_empty(client):
    """GET /api/config/token-usage should return zeros when no runs exist."""
    with patch("app.routers.config_router._read_config_json", return_value={"limits": {}}):
        resp = await client.get("/api/config/token-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"]["tokens_total"] == 0
    assert data["monthly"]["tokens_total"] == 0
    assert data["max_usage_percent"] == 80  # default
    assert data["reserve_percent"] == 20  # default


@pytest.mark.asyncio
async def test_get_token_usage_with_runs(client):
    """GET /api/config/token-usage should aggregate tokens from recent runs."""
    from datetime import datetime

    async with async_session() as session:
        project = Project(repo="owner/repo", priority="medium", mode="full", enabled=True, branch="main")
        session.add(project)
        await session.flush()
        run = Run(
            run_id="run-token-001",
            project_id=project.id,
            status="completed",
            tokens_input=10000,
            tokens_output=5000,
            tokens_total=15000,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()

    with patch("app.routers.config_router._read_config_json", return_value={"limits": {}}):
        resp = await client.get("/api/config/token-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"]["tokens_total"] == 15000
    assert data["daily"]["tokens_input"] == 10000
    assert data["daily"]["tokens_output"] == 5000


# ---------------------------------------------------------------------------
# Unit tests for limit migration
# ---------------------------------------------------------------------------

def test_migrate_limits_removes_old_fields():
    """_migrate_limits_in_memory should remove old limit fields."""
    from app.routers.config_router import _migrate_limits_in_memory

    old = {
        "token_limit_daily": 100000,
        "token_limit_monthly": 3000000,
        "token_reserve_percent": 10,
        "session_limit_24h": 50,
        "max_session_percent": 90,
    }
    result = _migrate_limits_in_memory(old)
    assert "token_limit_daily" not in result
    assert "session_limit_24h" not in result
    assert result["max_usage_percent"] == 90
    assert result["reserve_percent"] == 10


def test_migrate_limits_adds_defaults():
    """_migrate_limits_in_memory should add defaults for missing new fields."""
    from app.routers.config_router import _migrate_limits_in_memory

    result = _migrate_limits_in_memory({})
    assert result["max_usage_percent"] == 80
    assert result["reserve_percent"] == 20
