"""Tests for the webhook endpoint.

Covers:
- Authentication: token required/rejected/accepted, backward compat
- Event types: run_start, employee_complete, manager_review, verdict_execute,
  run_complete, task_started, task_completed
- Event normalization: employee_complete -> employee_done (not finished)
- Unknown events handled gracefully
- DB writes for each event type
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import CoordinatorMessage, CoordinatorTask, Notification, Project, Run

VALID_EVENT = {
    "event": "run_start",
    "run_id": "run-test-123",
    "project": "owner/repo",
}


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
    """Insert a sample project for webhook tests."""
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


@pytest_asyncio.fixture
async def project_client(sample_project):
    """Provide an async HTTP client with a sample project in DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth enforcement when webhook_secret is set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_missing_token_when_secret_set(client: AsyncClient):
    """Request without X-Webhook-Token header should get 401."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 401
    assert "Invalid or missing webhook token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_rejects_wrong_token_when_secret_set(client: AsyncClient):
    """Request with incorrect X-Webhook-Token header should get 401."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post(
            "/api/webhook/run-event",
            json=VALID_EVENT,
            headers={"X-Webhook-Token": "wrong-token"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_accepts_correct_token(client: AsyncClient):
    """Request with correct X-Webhook-Token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = "my-secret-token"
        resp = await client.post(
            "/api/webhook/run-event",
            json=VALID_EVENT,
            headers={"X-Webhook-Token": "my-secret-token"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["run_id"] == "run-test-123"


# ---------------------------------------------------------------------------
# Backward compatibility — no secret configured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_secret_allows_unauthenticated(client: AsyncClient):
    """When webhook_secret is empty/None, requests without token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = None
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_empty_secret_allows_unauthenticated(client: AsyncClient):
    """When webhook_secret is an empty string, requests without token should succeed."""
    with patch("app.routers.webhook.settings") as mock_settings:
        mock_settings.webhook_secret = ""
        resp = await client.post("/api/webhook/run-event", json=VALID_EVENT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Event type: run_start — creates a Run record with status=running
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_start_event_creates_run(project_client: AsyncClient):
    """POST run_start should create a Run record with status=running."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-start-001",
        "event": "run_start",
        "project": "owner/test-repo",
        "mode": "employee",
        "model": "claude-sonnet-4-20250514",
        "employee_index": 0,
        "concurrent_group_id": "group-A",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-start-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "running"
        assert run.mode == "employee"
        assert run.model == "claude-sonnet-4-20250514"
        assert run.employee_index == 0
        assert run.concurrent_group_id == "group-A"
        assert run.started_at is not None
        assert run.project_id is not None


@pytest.mark.asyncio
async def test_run_start_updates_existing_run(project_client: AsyncClient):
    """If run_id already exists, run_start should update it."""
    # First create it
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-existing-001",
        "event": "run_start",
        "project": "owner/test-repo",
    })
    # Send another run_start for the same run_id
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-existing-001",
        "event": "run_start",
        "project": "owner/test-repo",
        "mode": "manager",
        "model": "claude-opus-4-20250514",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-existing-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "running"
        assert run.mode == "manager"
        assert run.model == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# Event normalization: employee_complete -> employee_done (NOT finished)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_employee_complete_maps_to_employee_done(project_client: AsyncClient):
    """employee_complete should normalize to employee_done, keeping status=running."""
    # First create a run
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-emp-001",
        "event": "run_start",
        "project": "owner/test-repo",
        "mode": "employee",
    })

    # Send employee_complete
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-emp-001",
        "event": "employee_complete",
        "project": "owner/test-repo",
        "mode": "employee",
        "model": "claude-sonnet-4-20250514",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-emp-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        # Critical: status should still be "running", NOT "completed"
        assert run.status == "running"


@pytest.mark.asyncio
async def test_employee_complete_creates_run_if_missing(project_client: AsyncClient):
    """employee_complete for unknown run_id should create a run with status=running."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-emp-new-001",
        "event": "employee_complete",
        "project": "owner/test-repo",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-emp-new-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "running"


# ---------------------------------------------------------------------------
# Event type: manager_review -> reviewing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manager_review_sets_reviewing_status(project_client: AsyncClient):
    """manager_review should set status to 'reviewing'."""
    # Create run first
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-review-001",
        "event": "run_start",
        "project": "owner/test-repo",
    })

    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-review-001",
        "event": "manager_review",
        "project": "owner/test-repo",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-review-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "reviewing"


# ---------------------------------------------------------------------------
# Event type: verdict_execute -> verdict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.webhook.send_notification", new_callable=AsyncMock)
async def test_verdict_execute_updates_verdict(mock_notify, project_client: AsyncClient):
    """verdict_execute should set verdict, issue_number, branch, and create notification."""
    # Create run first
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-verdict-001",
        "event": "run_start",
        "project": "owner/test-repo",
    })

    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-verdict-001",
        "event": "verdict_execute",
        "project": "owner/test-repo",
        "verdict": "APPROVE",
        "issue_number": 42,
        "branch": "autonomous/issue-42",
        "reasoning": "Clean implementation, all tests pass",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-verdict-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.verdict == "APPROVE"
        assert run.issue_number == 42
        assert run.branch == "autonomous/issue-42"
        assert run.verdict_detail is not None
        detail = json.loads(run.verdict_detail)
        assert detail["verdict"] == "APPROVE"
        assert detail["reasoning"] == "Clean implementation, all tests pass"

        # Notification should be created
        notif_result = await db.execute(
            select(Notification).where(Notification.run_id == "run-verdict-001")
        )
        notif = notif_result.scalar_one_or_none()
        assert notif is not None
        assert notif.type == "approve"
        assert "approved" in notif.message.lower()


@pytest.mark.asyncio
@patch("app.routers.webhook.send_notification", new_callable=AsyncMock)
async def test_verdict_reject_creates_notification(mock_notify, project_client: AsyncClient):
    """verdict_execute with REJECT should create a reject notification."""
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-verdict-rej-001",
        "event": "run_start",
        "project": "owner/test-repo",
    })

    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-verdict-rej-001",
        "event": "verdict_execute",
        "project": "owner/test-repo",
        "verdict": "REJECT",
        "reasoning": "Tests failing",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        notif_result = await db.execute(
            select(Notification).where(Notification.run_id == "run-verdict-rej-001")
        )
        notif = notif_result.scalar_one_or_none()
        assert notif is not None
        assert notif.type == "reject"
        assert "rejected" in notif.message.lower()


# ---------------------------------------------------------------------------
# Event type: run_complete -> finished
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.run_lifecycle.parse_employee_report", return_value=None)
async def test_run_complete_marks_finished(mock_report, project_client: AsyncClient):
    """run_complete should set status=completed and populate token/timing fields."""
    # Create run first
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-complete-001",
        "event": "run_start",
        "project": "owner/test-repo",
    })

    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-complete-001",
        "event": "run_complete",
        "project": "owner/test-repo",
        "status": "success",
        "tokens_input": 40000,
        "tokens_output": 10000,
        "tokens_total": 50000,
        "turns": 30,
        "duration_ms": 120000,
        "model": "claude-sonnet-4-20250514",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-complete-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "completed"  # "success" maps to "completed"
        assert run.tokens_input == 40000
        assert run.tokens_output == 10000
        assert run.tokens_total == 50000
        assert run.turns == 30
        assert run.duration_ms == 120000
        assert run.finished_at is not None


@pytest.mark.asyncio
@patch("app.services.run_lifecycle.parse_employee_report", return_value=None)
async def test_run_complete_creates_run_if_missing(mock_report, project_client: AsyncClient):
    """run_complete for unknown run_id should create a run with the final status."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-complete-new-001",
        "event": "run_complete",
        "project": "owner/test-repo",
        "status": "success",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-complete-new-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "completed"


@pytest.mark.asyncio
@patch("app.services.run_lifecycle.parse_employee_report", return_value={"issue_title": "Test"})
async def test_run_complete_reads_employee_report(mock_report, project_client: AsyncClient):
    """run_complete should attempt to read employee report from disk."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-complete-report-001",
        "event": "run_complete",
        "project": "owner/test-repo",
        "status": "success",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-complete-report-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.employee_report is not None
        report = json.loads(run.employee_report)
        assert report["issue_title"] == "Test"


# ---------------------------------------------------------------------------
# Coordinator task events: task_started, task_completed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_started_creates_coordinator_task(project_client: AsyncClient):
    """task_started should create a CoordinatorTask record."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-task-001",
        "event": "task_started",
        "project": "owner/test-repo",
        "task_id": "task-run-task-001-1",
        "task_title": "Create database schema",
        "employee_index": 0,
        "depends_on": "[]",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(
            select(CoordinatorTask).where(CoordinatorTask.id == "task-run-task-001-1")
        )
        ctask = result.scalar_one_or_none()
        assert ctask is not None
        assert ctask.status == "running"
        assert ctask.title == "Create database schema"
        assert ctask.employee_index == 0
        assert ctask.started_at is not None


@pytest.mark.asyncio
async def test_task_completed_updates_coordinator_task(project_client: AsyncClient):
    """task_completed should update the CoordinatorTask status and finished_at."""
    # Create task first
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-task-002",
        "event": "task_started",
        "project": "owner/test-repo",
        "task_id": "task-run-task-002-1",
        "task_title": "Build API",
        "employee_index": 1,
    })

    # Complete it
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-task-002",
        "event": "task_completed",
        "project": "owner/test-repo",
        "task_id": "task-run-task-002-1",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(
            select(CoordinatorTask).where(CoordinatorTask.id == "task-run-task-002-1")
        )
        ctask = result.scalar_one_or_none()
        assert ctask is not None
        assert ctask.status == "completed"
        assert ctask.finished_at is not None


@pytest.mark.asyncio
async def test_task_failed_updates_coordinator_task(project_client: AsyncClient):
    """task_failed should update the CoordinatorTask status to failed."""
    await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-task-003",
        "event": "task_started",
        "project": "owner/test-repo",
        "task_id": "task-run-task-003-1",
        "task_title": "Run tests",
        "employee_index": 2,
    })

    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-task-003",
        "event": "task_failed",
        "project": "owner/test-repo",
        "task_id": "task-run-task-003-1",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(
            select(CoordinatorTask).where(CoordinatorTask.id == "task-run-task-003-1")
        )
        ctask = result.scalar_one_or_none()
        assert ctask is not None
        assert ctask.status == "failed"
        assert ctask.finished_at is not None


# ---------------------------------------------------------------------------
# Coordinator messages: conflict_detected, guidance_sent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_detected_creates_message(project_client: AsyncClient):
    """conflict_detected should create a CoordinatorMessage."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-conflict-001",
        "event": "conflict_detected",
        "project": "owner/test-repo",
        "file_path": "src/main.py",
        "employee_a": 0,
        "employee_b": 1,
        "employee_index": 0,
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(
            select(CoordinatorMessage).where(CoordinatorMessage.run_id == "run-conflict-001")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.direction == "from_monitor"
        assert msg.message_type == "conflict"
        content = json.loads(msg.content)
        assert content["file_path"] == "src/main.py"
        assert content["employee_a"] == 0
        assert content["employee_b"] == 1


@pytest.mark.asyncio
async def test_guidance_sent_creates_message(project_client: AsyncClient):
    """guidance_sent should create a CoordinatorMessage."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-guidance-001",
        "event": "guidance_sent",
        "project": "owner/test-repo",
        "employee_index": 1,
        "guidance_type": "redirect",
        "guidance_content": "Use types.ts from employee 0",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(
            select(CoordinatorMessage).where(CoordinatorMessage.run_id == "run-guidance-001")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.direction == "to_employee"
        assert msg.message_type == "guidance"
        content = json.loads(msg.content)
        assert content["guidance_type"] == "redirect"
        assert content["guidance_content"] == "Use types.ts from employee 0"


# ---------------------------------------------------------------------------
# Unknown event — handled gracefully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_event_creates_run(project_client: AsyncClient):
    """Unknown event name should still create a Run record gracefully."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-unknown-001",
        "event": "some_new_event",
        "project": "owner/test-repo",
        "mode": "employee",
        "status": "special",
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-unknown-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.status == "special"
        assert run.mode == "employee"


# ---------------------------------------------------------------------------
# Project matching: short name matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_matched_by_short_name(project_client: AsyncClient):
    """Webhook should match project by short repo name if full name doesn't match."""
    resp = await project_client.post("/api/webhook/run-event", json={
        "run_id": "run-short-001",
        "event": "run_start",
        "project": "different-owner/test-repo",  # different owner, same repo name
    })
    assert resp.status_code == 200

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-short-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.project_id is not None  # should match by short name


# ---------------------------------------------------------------------------
# Event normalization unit tests
# ---------------------------------------------------------------------------

def test_normalize_event_name():
    """Test the _normalize_event_name function directly."""
    from app.routers.webhook import _normalize_event_name

    assert _normalize_event_name("run_start") == "started"
    assert _normalize_event_name("employee_start") == "started"
    assert _normalize_event_name("employee_complete") == "employee_done"
    assert _normalize_event_name("manager_review") == "reviewing"
    assert _normalize_event_name("run_complete") == "finished"
    assert _normalize_event_name("verdict_execute") == "verdict"
    # Legacy names pass through
    assert _normalize_event_name("started") == "started"
    assert _normalize_event_name("finished") == "finished"
    assert _normalize_event_name("verdict") == "verdict"
    # Coordinator events pass through
    assert _normalize_event_name("task_started") == "task_started"
    assert _normalize_event_name("task_completed") == "task_completed"
    assert _normalize_event_name("task_failed") == "task_failed"
    assert _normalize_event_name("conflict_detected") == "conflict_detected"
    assert _normalize_event_name("guidance_sent") == "guidance_sent"
    # Unknown returns itself
    assert _normalize_event_name("unknown_event") == "unknown_event"


# ---------------------------------------------------------------------------
# SSE broadcast: vision-bootstrap fields propagated to event bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.run_lifecycle.parse_employee_report", return_value=None)
async def test_run_event_publishes_vision_bootstrap_fields_to_sse(
    mock_report, project_client: AsyncClient
):
    """Webhook 'finished' for vision-bootstrap should propagate vision_bootstrap_count,
    vision_bootstrap_proposals, and skip_reason to the SSE event bus payload."""
    published: list[dict] = []

    async def capture_publish(payload: dict) -> None:
        published.append(payload)

    with patch("app.routers.webhook.event_bus_publish", side_effect=capture_publish):
        resp = await project_client.post("/api/webhook/run-event", json={
            "run_id": "run-vb-sse-001",
            "event": "run_complete",
            "project": "owner/test-repo",
            "mode": "vision-bootstrap",
            "status": "success",
            "vision_bootstrap_count": 3,
            "vision_bootstrap_proposals": [
                {"title": "Add logging", "body": "We need logs."},
                {"title": "Add tests", "body": "Coverage gap."},
                {"title": "Add docs", "body": "Docs are missing."},
            ],
            "skip_reason": None,
        })
    assert resp.status_code == 200

    # At least one published event should carry the vision-bootstrap fields
    assert len(published) >= 1, "event_bus_publish was never called"
    data_blocks = [p.get("data", {}) for p in published]
    matching = [
        d for d in data_blocks
        if d.get("vision_bootstrap_count") is not None
        or d.get("vision_bootstrap_proposals") is not None
    ]
    assert matching, (
        "None of the published SSE events carried vision_bootstrap_count or "
        "vision_bootstrap_proposals. "
        f"Published data blocks: {data_blocks}"
    )
    sse_data = matching[0]
    assert sse_data["vision_bootstrap_count"] == 3
    assert isinstance(sse_data["vision_bootstrap_proposals"], list)
    assert len(sse_data["vision_bootstrap_proposals"]) == 3
    assert sse_data["vision_bootstrap_proposals"][0]["title"] == "Add logging"


@pytest.mark.asyncio
@patch("app.services.run_lifecycle.parse_employee_report", return_value=None)
async def test_run_event_publishes_skip_reason_to_sse(
    mock_report, project_client: AsyncClient
):
    """Webhook 'finished' for vision-bootstrap with skip_reason should propagate
    the skip_reason field in the SSE payload."""
    published: list[dict] = []

    async def capture_publish(payload: dict) -> None:
        published.append(payload)

    with patch("app.routers.webhook.event_bus_publish", side_effect=capture_publish):
        resp = await project_client.post("/api/webhook/run-event", json={
            "run_id": "run-vb-sse-002",
            "event": "run_complete",
            "project": "owner/test-repo",
            "mode": "vision-bootstrap",
            "status": "success",
            "vision_bootstrap_count": 0,
            "vision_bootstrap_proposals": [],
            "skip_reason": "no_vision_document",
        })
    assert resp.status_code == 200

    data_blocks = [p.get("data", {}) for p in published]
    matching = [d for d in data_blocks if d.get("skip_reason") is not None]
    assert matching, (
        "No published SSE event carried skip_reason. "
        f"Published data blocks: {data_blocks}"
    )
    assert matching[0]["skip_reason"] == "no_vision_document"


def test_build_notification_message():
    """Test the _build_notification_message function."""
    from app.services.run_lifecycle import build_notification_message as _build_notification_message
    from app.schemas import WebhookRunEvent

    approve_event = WebhookRunEvent(
        run_id="r1", event="verdict_execute",
        project="owner/repo", verdict="APPROVE",
    )
    assert "approved" in _build_notification_message(approve_event).lower()

    pr_event = WebhookRunEvent(
        run_id="r2", event="verdict_execute",
        project="owner/repo", verdict="PR", issue_number=42,
    )
    msg = _build_notification_message(pr_event)
    assert "PR" in msg
    assert "#42" in msg

    reject_event = WebhookRunEvent(
        run_id="r3", event="verdict_execute",
        project="owner/repo", verdict="REJECT", reasoning="Bad code",
    )
    assert "rejected" in _build_notification_message(reject_event).lower()
