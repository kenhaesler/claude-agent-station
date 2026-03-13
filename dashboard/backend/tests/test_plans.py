"""Tests for Plan model and CRUD endpoints.

Covers:
- Timezone-aware timestamps (Plan.created_at, Plan.updated_at use _utcnow)
- POST /api/plans — create a plan
- GET /api/plans — list plans with filters
- GET /api/plans/{id} — get single plan
- PUT /api/plans/{id} — update plan fields
- DELETE /api/plans/{id} — delete plan
- POST /api/plans/{id}/approve — approve a plan
- POST /api/plans/{id}/reject — reject a plan
- Status transition validation
"""

import pytest
import pytest_asyncio
from datetime import timezone
from unittest.mock import patch, AsyncMock

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import engine, Base, async_session
from app.models import Project, Plan, _utcnow


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def sample_project(setup_db):
    """Insert a sample project for tests that need one."""
    async with async_session() as session:
        project = Project(
            repo="owner/plan-test-repo",
            priority="medium",
            mode="full",
            enabled=True,
            branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def project_client(sample_project):
    """Provide an async HTTP client with a sample project in DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, sample_project


# ---------------------------------------------------------------------------
# Timestamp tests (existing)
# ---------------------------------------------------------------------------

def test_utcnow_returns_timezone_aware():
    """_utcnow() helper should return a timezone-aware datetime with UTC tzinfo."""
    dt = _utcnow()
    assert dt.tzinfo is not None, "_utcnow should return timezone-aware datetime"
    assert dt.tzinfo == timezone.utc


def test_plan_created_at_default_is_utcnow():
    """Plan.created_at column default should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.created_at
    assert col.default is not None
    assert col.default.arg.__name__ == "_utcnow", (
        f"Plan.created_at default should be _utcnow, got {col.default.arg.__name__}"
    )


def test_plan_updated_at_default_is_utcnow():
    """Plan.updated_at column default should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.updated_at
    assert col.default is not None
    assert col.default.arg.__name__ == "_utcnow", (
        f"Plan.updated_at default should be _utcnow, got {col.default.arg.__name__}"
    )


def test_plan_updated_at_onupdate_is_utcnow():
    """Plan.updated_at onupdate should use the _utcnow helper, not datetime.utcnow."""
    col = Plan.__table__.c.updated_at
    assert col.onupdate is not None
    assert col.onupdate.arg.__name__ == "_utcnow", (
        f"Plan.updated_at onupdate should be _utcnow, got {col.onupdate.arg.__name__}"
    )


@pytest.mark.asyncio
async def test_plan_timestamps_consistent_with_project(sample_project):
    """Plan timestamps should be comparable with Project timestamps (both use same default)."""
    async with async_session() as session:
        plan = Plan(
            project_id=sample_project.id,
            title="Test plan",
            status="draft",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

        # Both should have timestamps set
        assert plan.created_at is not None
        assert plan.updated_at is not None
        assert sample_project.created_at is not None


# ---------------------------------------------------------------------------
# POST /api/plans — create plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_plan(project_client):
    """POST /api/plans should create a plan with correct fields."""
    client, project = project_client
    resp = await client.post("/api/plans", json={
        "project_id": project.id,
        "title": "Add login feature",
        "description": "Implement OAuth login",
        "issue_number": 42,
        "issue_title": "Login feature",
        "steps": '["Step 1", "Step 2"]',
        "estimated_scope": "medium",
        "files_affected": '["src/auth.py"]',
        "status": "draft",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Add login feature"
    assert data["description"] == "Implement OAuth login"
    assert data["project_id"] == project.id
    assert data["issue_number"] == 42
    assert data["issue_title"] == "Login feature"
    assert data["status"] == "draft"
    assert data["estimated_scope"] == "medium"
    assert data["id"] is not None
    assert data["created_at"] is not None


@pytest.mark.asyncio
async def test_create_plan_minimal(project_client):
    """POST /api/plans should work with only required fields."""
    client, project = project_client
    resp = await client.post("/api/plans", json={
        "project_id": project.id,
        "title": "Minimal plan",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Minimal plan"
    assert data["status"] == "draft"


@pytest.mark.asyncio
async def test_create_plan_invalid_project(client):
    """POST /api/plans with nonexistent project_id should return 404."""
    resp = await client.post("/api/plans", json={
        "project_id": 99999,
        "title": "Bad plan",
    })
    assert resp.status_code == 404
    assert "Project not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/plans — list plans
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_plans_empty(client):
    """GET /api/plans should return empty list when no plans exist."""
    resp = await client.get("/api/plans")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plans"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_plans(project_client):
    """GET /api/plans should return all plans."""
    client, project = project_client
    # Create two plans
    await client.post("/api/plans", json={
        "project_id": project.id, "title": "Plan A",
    })
    await client.post("/api/plans", json={
        "project_id": project.id, "title": "Plan B",
    })

    resp = await client.get("/api/plans")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["plans"]) == 2


@pytest.mark.asyncio
async def test_list_plans_filter_by_project_id(project_client):
    """GET /api/plans?project_id=X should filter by project."""
    client, project = project_client
    await client.post("/api/plans", json={
        "project_id": project.id, "title": "Plan A",
    })

    resp = await client.get(f"/api/plans?project_id={project.id}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get("/api/plans?project_id=99999")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_plans_filter_by_status(project_client):
    """GET /api/plans?status=draft should filter by status."""
    client, project = project_client
    await client.post("/api/plans", json={
        "project_id": project.id, "title": "Draft plan", "status": "draft",
    })
    await client.post("/api/plans", json={
        "project_id": project.id, "title": "Approved plan", "status": "approved",
    })

    resp = await client.get("/api/plans?status=draft")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["plans"][0]["title"] == "Draft plan"


@pytest.mark.asyncio
async def test_list_plans_pagination(project_client):
    """GET /api/plans respects limit and offset."""
    client, project = project_client
    for i in range(5):
        await client.post("/api/plans", json={
            "project_id": project.id, "title": f"Plan {i}",
        })

    resp = await client.get("/api/plans?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["plans"]) == 2


# ---------------------------------------------------------------------------
# GET /api/plans/{id} — get single plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan(project_client):
    """GET /api/plans/{id} should return the plan."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "My plan",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.get(f"/api/plans/{plan_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "My plan"


@pytest.mark.asyncio
async def test_get_plan_not_found(client):
    """GET /api/plans/{id} should return 404 for nonexistent plan."""
    resp = await client.get("/api/plans/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/plans/{id} — update plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_plan(project_client):
    """PUT /api/plans/{id} should update specified fields."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "Original title",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.put(f"/api/plans/{plan_id}", json={
        "title": "Updated title",
        "description": "New description",
        "status": "approved",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated title"
    assert data["description"] == "New description"
    assert data["status"] == "approved"


@pytest.mark.asyncio
async def test_update_plan_partial(project_client):
    """PUT /api/plans/{id} should only update provided fields."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id,
        "title": "Original",
        "description": "Keep me",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.put(f"/api/plans/{plan_id}", json={
        "title": "Changed",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Changed"
    assert data["description"] == "Keep me"  # unchanged


@pytest.mark.asyncio
async def test_update_plan_not_found(client):
    """PUT /api/plans/{id} should return 404 for nonexistent plan."""
    resp = await client.put("/api/plans/99999", json={"title": "X"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/plans/{id} — delete plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_plan(project_client):
    """DELETE /api/plans/{id} should remove the plan."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "To delete",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/plans/{plan_id}")
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(f"/api/plans/{plan_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_plan_not_found(client):
    """DELETE /api/plans/{id} should return 404 for nonexistent plan."""
    resp = await client.delete("/api/plans/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/plans/{id}/approve — approve plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_plan(project_client):
    """POST /api/plans/{id}/approve should set status to approved."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "Approvable plan", "status": "draft",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.post(f"/api/plans/{plan_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_rejected_plan(project_client):
    """POST /api/plans/{id}/approve should work for rejected plans too."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "Rejected plan", "status": "draft",
    })
    plan_id = create_resp.json()["id"]
    # Reject it first
    await client.post(f"/api/plans/{plan_id}/reject")
    # Then approve
    resp = await client.post(f"/api/plans/{plan_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_plan_invalid_status(project_client):
    """POST /api/plans/{id}/approve should fail for implementing/completed plans."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "Implementing plan", "status": "draft",
    })
    plan_id = create_resp.json()["id"]
    # Set to approved then implementing via update
    await client.put(f"/api/plans/{plan_id}", json={"status": "implementing"})

    resp = await client.post(f"/api/plans/{plan_id}/approve")
    assert resp.status_code == 400
    assert "Cannot approve" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_approve_plan_not_found(client):
    """POST /api/plans/{id}/approve should return 404 for nonexistent plan."""
    resp = await client.post("/api/plans/99999/approve")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/plans/{id}/reject — reject plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_plan(project_client):
    """POST /api/plans/{id}/reject should set status to rejected."""
    client, project = project_client
    create_resp = await client.post("/api/plans", json={
        "project_id": project.id, "title": "Rejectable plan",
    })
    plan_id = create_resp.json()["id"]

    resp = await client.post(f"/api/plans/{plan_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_reject_plan_not_found(client):
    """POST /api/plans/{id}/reject should return 404 for nonexistent plan."""
    resp = await client.post("/api/plans/99999/reject")
    assert resp.status_code == 404
