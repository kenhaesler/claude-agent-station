"""Operator override of the plan-review gate (issue #266 follow-up).

The auto-gate (manager's APPROVE_PLAN verdict triggering an enqueue) lives
in :mod:`agent.plan_review_gate`. The endpoints under test here run from
the dashboard backend and let the operator force-approve or force-reject
a plan_only run that's stuck at ``awaiting_plan_review``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, QueueItem, Run, StationControl


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        session.add(StationControl(id=1, global_pause=False))
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def awaiting_run(setup_db):
    async with async_session() as session:
        proj = Project(repo="x/y", priority="medium", mode="plan_only", branch="main")
        session.add(proj)
        await session.flush()
        run = Run(
            run_id="run-pr-001",
            project_id=proj.id,
            mode="plan_only",
            status="awaiting_plan_review",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
        return run.run_id


def _write_verdicts(tmp_path, run_id: str, plan_verdicts: list[dict]) -> str:
    """Write a manager-verdicts JSON file at the path
    ``run-manager.sh`` would have written and return the directory so
    callers can monkeypatch ``STATION_LOG_DIR``."""
    log_dir = tmp_path / "claude-agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{run_id}-verdicts.json").write_text(json.dumps({
        "run_id": run_id,
        "verdicts": [],
        "plan_verdicts": plan_verdicts,
    }))
    return str(log_dir)


@pytest.mark.asyncio
async def test_approve_unknown_run_returns_404(client):
    resp = await client.post("/api/runs/does-not-exist/plan/approve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_rejects_full_mode_run(client, setup_db):
    """Operator override is only valid for plan_only runs. A full-mode run
    must be rejected with 409 — bypassing review for a run that wasn't
    designed to gate would be confusing.
    """
    async with async_session() as session:
        proj = Project(repo="x/y", priority="medium", mode="full", branch="main")
        session.add(proj)
        await session.flush()
        run = Run(
            run_id="run-full-001",
            project_id=proj.id,
            mode="full",
            status="awaiting_plan_review",  # impossible state, but exercise the guard
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
    resp = await client.post("/api/runs/run-full-001/plan/approve")
    assert resp.status_code == 409
    assert "plan_only" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_approve_rejects_run_in_terminal_state(client, awaiting_run):
    """Once the gate has resolved (either auto-approved by manager or
    rejected), operator override is no longer valid — the follow-up
    queue item or rejection has already been recorded.
    """
    async with async_session() as session:
        run = (
            await session.execute(select(Run).where(Run.run_id == awaiting_run))
        ).scalar_one()
        run.status = "plan_approved"
        await session.commit()
    resp = await client.post(f"/api/runs/{awaiting_run}/plan/approve")
    assert resp.status_code == 409
    assert "awaiting_plan_review" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_approve_with_verdicts_file_enqueues_followup(
    client, awaiting_run, tmp_path, monkeypatch
):
    """Happy path: operator approves a plan_only run that has a
    manager-verdicts file. The endpoint reads the file, enqueues a
    follow-up ``full`` QueueItem per plan entry (regardless of what the
    manager said), and flips the run to ``plan_approved``.
    """
    log_dir = _write_verdicts(tmp_path, awaiting_run, [
        {
            "verdict": "REVISE_PLAN",  # manager said revise; operator overrides
            "employee_index": 0,
            "issue_number": 42,
            "plan_path": "/ws/.claude-employee-plan-0.json",
            "feedback": "needs more detail",
        },
        {
            "verdict": "REJECT_PLAN",  # operator overrides REJECT too
            "employee_index": 1,
            "issue_number": 43,
            "plan_path": "/ws/.claude-employee-plan-1.json",
            "feedback": "no good",
        },
    ])
    monkeypatch.setenv("STATION_LOG_DIR", log_dir)

    resp = await client.post(f"/api/runs/{awaiting_run}/plan/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "plan_approved"
    assert body["verdicts_file_found"] is True
    assert len(body["enqueued"]) == 2
    assert {e["issue_number"] for e in body["enqueued"]} == {42, 43}

    async with async_session() as session:
        run = (
            await session.execute(select(Run).where(Run.run_id == awaiting_run))
        ).scalar_one()
        assert run.status == "plan_approved"
        assert run.finished_at is not None

        items = (
            await session.execute(
                select(QueueItem).where(QueueItem.project_repo == "x/y")
            )
        ).scalars().all()
        assert len(items) == 2
        for qi in items:
            assert qi.mode == "full"
            assert qi.state == "pending"
            ctx = json.loads(qi.context)
            assert ctx["from_plan_only_run"] is True
            assert ctx["approved_by_operator"] is True
            assert ctx["parent_run_id"] == awaiting_run


@pytest.mark.asyncio
async def test_approve_without_verdicts_file_still_advances_run(
    client, awaiting_run, tmp_path, monkeypatch
):
    """When the verdicts file is missing (manager didn't produce one),
    the operator can still advance the run out of awaiting_plan_review
    so the dashboard stops showing the gate banner. No follow-ups are
    enqueued — the operator hasn't bound implementation to specific
    issues, so we don't enqueue anything speculative.
    """
    monkeypatch.setenv("STATION_LOG_DIR", str(tmp_path / "empty-log-dir"))

    resp = await client.post(f"/api/runs/{awaiting_run}/plan/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "plan_approved"
    assert body["verdicts_file_found"] is False
    assert body["enqueued"] == []

    async with async_session() as session:
        items = (
            await session.execute(select(QueueItem))
        ).scalars().all()
        assert items == []


@pytest.mark.asyncio
async def test_approve_with_malformed_verdicts_file_logs_and_advances(
    client, awaiting_run, tmp_path, monkeypatch, caplog
):
    """Malformed JSON in the verdicts file must not block the operator
    override — log a warning and proceed with the status flip."""
    log_dir = tmp_path / "bad-log-dir"
    log_dir.mkdir()
    (log_dir / f"{awaiting_run}-verdicts.json").write_text("{not json")
    monkeypatch.setenv("STATION_LOG_DIR", str(log_dir))

    with caplog.at_level("WARNING"):
        resp = await client.post(f"/api/runs/{awaiting_run}/plan/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "plan_approved"
    assert any("could not parse verdicts file" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_reject_unknown_run_returns_404(client):
    resp = await client.post("/api/runs/does-not-exist/plan/reject")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reject_rejects_full_mode_run(client, setup_db):
    async with async_session() as session:
        proj = Project(repo="x/y", priority="medium", mode="full", branch="main")
        session.add(proj)
        await session.flush()
        run = Run(
            run_id="run-full-002",
            project_id=proj.id,
            mode="full",
            status="awaiting_plan_review",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.commit()
    resp = await client.post("/api/runs/run-full-002/plan/reject")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_reject_flips_status_and_enqueues_nothing(client, awaiting_run):
    resp = await client.post(f"/api/runs/{awaiting_run}/plan/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "plan_rejected"

    async with async_session() as session:
        run = (
            await session.execute(select(Run).where(Run.run_id == awaiting_run))
        ).scalar_one()
        assert run.status == "plan_rejected"
        assert run.finished_at is not None
        items = (
            await session.execute(select(QueueItem))
        ).scalars().all()
        assert items == []
