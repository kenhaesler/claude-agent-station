"""Tests for the projects CRUD API.

Covers:
- GET /api/projects — list all projects
- GET /api/projects/{id} — get single project
- POST /api/projects — create project
- PUT /api/projects/{id} — update project
- DELETE /api/projects/{id} — delete project with cascade
- 404 for missing project
- 409 for duplicate repo
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import Plan, Project, Run


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


@pytest_asyncio.fixture
async def sample_project(setup_db):
    """Insert a sample project for tests that need one."""
    async with async_session() as session:
        project = Project(
            repo="owner/test-repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


# --- List projects ---

@pytest.mark.asyncio
async def test_list_projects_empty(client):
    """GET /api/projects returns empty list when no projects exist."""
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_projects_with_data(client, sample_project):
    """GET /api/projects returns all projects."""
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["repo"] == "owner/test-repo"
    assert data[0]["priority"] == "medium"
    assert data[0]["enabled"] is True


# --- Get single project ---

@pytest.mark.asyncio
async def test_get_project(client, sample_project):
    """GET /api/projects/{id} returns the project."""
    resp = await client.get(f"/api/projects/{sample_project.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "owner/test-repo"
    assert data["id"] == sample_project.id


@pytest.mark.asyncio
async def test_get_project_not_found(client):
    """GET /api/projects/{id} returns 404 for unknown project."""
    resp = await client.get("/api/projects/9999")
    assert resp.status_code == 404


# --- Create project ---

@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_create_project(mock_sync, client):
    """POST /api/projects creates a new project."""
    resp = await client.post("/api/projects", json={
        "repo": "owner/new-repo",
        "priority": "high",
        "mode": "analyze",
        "branch": "develop",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["repo"] == "owner/new-repo"
    assert data["priority"] == "high"
    assert data["mode"] == "analyze"
    assert data["branch"] == "develop"
    assert data["enabled"] is True
    assert data["id"] is not None
    mock_sync.assert_called_once()


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_create_project_with_custom_instructions(mock_sync, client):
    """POST /api/projects supports custom_instructions and setup_script."""
    resp = await client.post("/api/projects", json={
        "repo": "owner/custom-repo",
        "custom_instructions": "Always use TypeScript",
        "setup_script": "npm install",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["custom_instructions"] == "Always use TypeScript"
    assert data["setup_script"] == "npm install"


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_create_project_duplicate_repo(mock_sync, client, sample_project):
    """POST /api/projects returns 409 for duplicate repo."""
    resp = await client.post("/api/projects", json={
        "repo": "owner/test-repo",
    })
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


# --- Update project ---

@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_update_project(mock_sync, client, sample_project):
    """PUT /api/projects/{id} updates fields."""
    resp = await client.put(f"/api/projects/{sample_project.id}", json={
        "priority": "high",
        "enabled": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["priority"] == "high"
    assert data["enabled"] is False
    # Unchanged fields should remain
    assert data["repo"] == "owner/test-repo"
    assert data["branch"] == "main"
    mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_update_project_not_found(client):
    """PUT /api/projects/{id} returns 404 for unknown project."""
    resp = await client.put("/api/projects/9999", json={"priority": "low"})
    assert resp.status_code == 404


# --- Delete project ---

@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_delete_project(mock_sync, client, sample_project):
    """DELETE /api/projects/{id} removes the project."""
    resp = await client.delete(f"/api/projects/{sample_project.id}")
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(f"/api/projects/{sample_project.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_not_found(client):
    """DELETE /api/projects/{id} returns 404 for unknown project."""
    resp = await client.delete("/api/projects/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_delete_project_nullifies_run_project_id(mock_sync, client, sample_project):
    """DELETE /api/projects/{id} sets project_id to NULL on related runs (preserves history)."""
    # Create a run linked to this project
    async with async_session() as session:
        run = Run(
            run_id="run-test-delete",
            project_id=sample_project.id,
            status="success",
        )
        session.add(run)
        await session.commit()

    resp = await client.delete(f"/api/projects/{sample_project.id}")
    assert resp.status_code == 204

    # Verify the run still exists but with project_id=None
    from sqlalchemy import select
    async with async_session() as session:
        result = await session.execute(
            select(Run).where(Run.run_id == "run-test-delete")
        )
        run = result.scalar_one()
        assert run.project_id is None


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_delete_project_deletes_related_plans(mock_sync, client, sample_project):
    """DELETE /api/projects/{id} cascades to delete related plans."""
    async with async_session() as session:
        plan = Plan(
            project_id=sample_project.id,
            title="Test plan",
            status="draft",
        )
        session.add(plan)
        await session.commit()

    resp = await client.delete(f"/api/projects/{sample_project.id}")
    assert resp.status_code == 204

    # Verify the plan is gone
    from sqlalchemy import select
    async with async_session() as session:
        result = await session.execute(
            select(Plan).where(Plan.project_id == sample_project.id)
        )
        assert result.scalar_one_or_none() is None
