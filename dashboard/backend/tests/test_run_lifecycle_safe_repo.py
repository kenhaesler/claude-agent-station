"""Tests for the defense-in-depth ``_safe_repo_short`` validator
introduced for issue #189 (path traversal in parse_employee_report).

The choke-point fix lives in ``log_parser.parse_employee_report``; this
validator runs at every webhook call site so attacker-controlled
``event.project`` values never reach the filesystem in the first place.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, Run
from app.services.run_lifecycle import _safe_repo_short


class TestSafeRepoShortUnit:
    """Pure-function tests for the validator -- no DB, no filesystem."""

    def test_returns_trailing_segment(self) -> None:
        assert _safe_repo_short("owner/my-repo") == "my-repo"

    def test_passes_bare_name(self) -> None:
        assert _safe_repo_short("my-repo") == "my-repo"

    def test_rejects_none(self) -> None:
        assert _safe_repo_short(None) is None

    def test_rejects_empty(self) -> None:
        assert _safe_repo_short("") is None

    def test_rejects_whitespace(self) -> None:
        assert _safe_repo_short("   ") is None

    def test_rejects_dot(self) -> None:
        assert _safe_repo_short(".") is None

    def test_rejects_double_dot(self) -> None:
        assert _safe_repo_short("..") is None

    def test_rejects_trailing_double_dot(self) -> None:
        # split("/")[-1] of "owner/.." is ".." -> rejected
        assert _safe_repo_short("owner/..") is None

    def test_rejects_dotdot_only_input(self) -> None:
        assert _safe_repo_short("..") is None

    def test_rejects_null_byte(self) -> None:
        assert _safe_repo_short("repo\x00.json") is None

    def test_rejects_backslash(self) -> None:
        # split("/") leaves backslashes intact; rejected as a defense-in-depth.
        assert _safe_repo_short("owner\\repo") is None


# ---------------------------------------------------------------------------
# Integration: webhook with malicious project must not call parse_employee_report
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def sample_project(setup_db):
    async with async_session() as db:
        # The project repo "owner/.." is a sentinel for the traversal test.
        # We register the legit project under a normal name; the malicious
        # webhook payload uses a different ``project`` value below.
        proj = Project(
            repo="owner/test-repo",
            enabled=True,
        )
        db.add(proj)
        await db.commit()
    yield


@pytest_asyncio.fixture
async def client(sample_project):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_traversal_project_does_not_invoke_parse_employee_report(
    client: AsyncClient,
) -> None:
    """A run_complete webhook with project='owner/..' must NOT pass through to
    parse_employee_report -- the validator strips the dangerous value first.
    """
    with patch(
        "app.services.run_lifecycle.parse_employee_report",
        return_value={"leaked": True},
    ) as mock_report:
        resp = await client.post(
            "/api/webhook/run-event",
            json={
                "run_id": "run-traversal-001",
                "event": "run_complete",
                "project": "owner/..",
                "status": "success",
            },
        )
        assert resp.status_code == 200
        mock_report.assert_not_called()

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-traversal-001"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.employee_report is None


@pytest.mark.asyncio
async def test_legitimate_project_still_invokes_parse_employee_report(
    client: AsyncClient,
) -> None:
    """Sanity check: the validator must not break the happy path."""
    with patch(
        "app.services.run_lifecycle.parse_employee_report",
        return_value={"issue_title": "ok"},
    ) as mock_report:
        resp = await client.post(
            "/api/webhook/run-event",
            json={
                "run_id": "run-traversal-002",
                "event": "run_complete",
                "project": "owner/test-repo",
                "status": "success",
            },
        )
        assert resp.status_code == 200
        mock_report.assert_called_once_with("test-repo")

    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == "run-traversal-002"))
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.employee_report is not None
        assert json.loads(run.employee_report) == {"issue_title": "ok"}


@pytest.mark.asyncio
async def test_verdict_with_traversal_does_not_invoke_parse_employee_report(
    client: AsyncClient,
) -> None:
    """The validator must also guard the handle_verdict call site."""
    with patch(
        "app.services.run_lifecycle.parse_employee_report",
        return_value={"leaked": True},
    ) as mock_report:
        resp = await client.post(
            "/api/webhook/run-event",
            json={
                "run_id": "run-verdict-trav-001",
                "event": "verdict_execute",
                "project": "owner/..",
                "verdict": "APPROVE",
                "issue_number": 1,
            },
        )
        assert resp.status_code == 200
        mock_report.assert_not_called()
