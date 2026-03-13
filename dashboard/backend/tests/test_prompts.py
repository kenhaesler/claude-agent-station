"""Tests for the prompts router.

Covers:
- GET /api/prompts — lists all prompt roles
- GET /api/prompts/{role} — gets a specific prompt
- PUT /api/prompts/{role} — saves a custom override
- DELETE /api/prompts/{role} — resets to default
- Invalid role returns 404
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import ConfigEntry


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
# GET /api/prompts — list all prompt roles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_prompts(client):
    """GET /api/prompts should return all defined prompt roles."""
    with patch("app.routers.prompts._read_default", return_value="default content"):
        resp = await client.get("/api/prompts")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 5  # manager, employee, analyst, planner, assigner
    roles = [p["role"] for p in data]
    assert "manager" in roles
    assert "employee" in roles
    assert "analyst" in roles
    assert "planner" in roles
    assert "assigner" in roles


@pytest.mark.asyncio
async def test_list_prompts_includes_labels(client):
    """GET /api/prompts should include label and description for each role."""
    with patch("app.routers.prompts._read_default", return_value=""):
        resp = await client.get("/api/prompts")
    assert resp.status_code == 200
    for prompt in resp.json():
        assert "label" in prompt
        assert "description" in prompt
        assert "has_override" in prompt


@pytest.mark.asyncio
async def test_list_prompts_shows_override(client):
    """GET /api/prompts should indicate when a custom override exists."""
    async with async_session() as session:
        entry = ConfigEntry(key="prompt_override_manager", value="custom manager prompt")
        session.add(entry)
        await session.commit()

    with patch("app.routers.prompts._read_default", return_value="default"):
        resp = await client.get("/api/prompts")
    assert resp.status_code == 200
    manager = next(p for p in resp.json() if p["role"] == "manager")
    assert manager["has_override"] is True
    assert manager["custom_content"] == "custom manager prompt"


# ---------------------------------------------------------------------------
# GET /api/prompts/{role} — get specific prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_prompt_valid_role(client):
    """GET /api/prompts/employee should return the employee prompt."""
    with patch("app.routers.prompts._read_default", return_value="You are an employee"):
        resp = await client.get("/api/prompts/employee")
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "employee"
    assert data["label"] == "Employee"
    assert data["default_content"] == "You are an employee"
    assert data["has_override"] is False


@pytest.mark.asyncio
async def test_get_prompt_invalid_role(client):
    """GET /api/prompts/nonexistent should return 404."""
    resp = await client.get("/api/prompts/nonexistent")
    assert resp.status_code == 404
    assert "Unknown prompt role" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_prompt_with_override(client):
    """GET /api/prompts/analyst should show override if present."""
    async with async_session() as session:
        entry = ConfigEntry(key="prompt_override_analyst", value="custom analyst")
        session.add(entry)
        await session.commit()

    with patch("app.routers.prompts._read_default", return_value="default analyst"):
        resp = await client.get("/api/prompts/analyst")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_override"] is True
    assert data["custom_content"] == "custom analyst"
    assert data["default_content"] == "default analyst"


# ---------------------------------------------------------------------------
# PUT /api/prompts/{role} — save custom override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_prompt(client):
    """PUT /api/prompts/manager should save a custom override."""
    with patch("app.routers.prompts._read_default", return_value="default"):
        with patch("app.routers.prompts._write_custom_file") as mock_write:
            resp = await client.put("/api/prompts/manager", json={
                "content": "You are a strict manager.",
            })
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_override"] is True
    assert data["custom_content"] == "You are a strict manager."
    mock_write.assert_called_once_with("manager", "You are a strict manager.")


@pytest.mark.asyncio
async def test_update_prompt_invalid_role(client):
    """PUT /api/prompts/nonexistent should return 404."""
    resp = await client.put("/api/prompts/nonexistent", json={"content": "test"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_prompt_empty_content(client):
    """PUT /api/prompts/manager with empty content should return 400."""
    resp = await client.put("/api/prompts/manager", json={"content": ""})
    assert resp.status_code == 400
    assert "non-empty" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_prompt_upsert(client):
    """PUT /api/prompts/employee should update existing override."""
    with patch("app.routers.prompts._read_default", return_value="default"):
        with patch("app.routers.prompts._write_custom_file"):
            await client.put("/api/prompts/employee", json={"content": "v1"})
            resp = await client.put("/api/prompts/employee", json={"content": "v2"})
    assert resp.status_code == 200
    assert resp.json()["custom_content"] == "v2"


# ---------------------------------------------------------------------------
# DELETE /api/prompts/{role} — reset to default
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_prompt(client):
    """DELETE /api/prompts/manager should remove the custom override."""
    # Create override first
    with patch("app.routers.prompts._read_default", return_value="default"):
        with patch("app.routers.prompts._write_custom_file"):
            await client.put("/api/prompts/manager", json={"content": "custom"})

    with patch("app.routers.prompts._read_default", return_value="default"):
        with patch("app.routers.prompts._delete_custom_file") as mock_delete:
            resp = await client.delete("/api/prompts/manager")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_override"] is False
    assert data["custom_content"] is None
    mock_delete.assert_called_once_with("manager")


@pytest.mark.asyncio
async def test_reset_prompt_invalid_role(client):
    """DELETE /api/prompts/nonexistent should return 404."""
    resp = await client.delete("/api/prompts/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reset_prompt_no_existing_override(client):
    """DELETE /api/prompts/planner should succeed even if no override exists."""
    with patch("app.routers.prompts._read_default", return_value="default"):
        with patch("app.routers.prompts._delete_custom_file"):
            resp = await client.delete("/api/prompts/planner")
    assert resp.status_code == 200
    assert resp.json()["has_override"] is False
