"""Tests for run_lifecycle handle_finished with vision-bootstrap fields."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.database import Base, async_session, engine
from app.services.run_lifecycle import handle_finished
from app.schemas import WebhookRunEvent


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_handle_finished_persists_vision_bootstrap_fields(setup_db):
    event = WebhookRunEvent(
        event="finished",
        run_id="run-vb-test-1",
        project="laboef1900/next-itsm",
        mode="vision-bootstrap",
        status="success",
        vision_bootstrap_count=3,
        vision_bootstrap_proposals=[
            {"number": 101, "title": "Add metrics dashboard", "url": "https://github.com/x/y/issues/101"},
            {"number": 102, "title": "Document API", "url": "https://github.com/x/y/issues/102"},
            {"number": 103, "title": "Add CI", "url": "https://github.com/x/y/issues/103"},
        ],
    )
    async with async_session() as db:
        run = await handle_finished(db, event, project_id=None, run=None)
        await db.commit()
        await db.refresh(run)
        assert run.mode == "vision-bootstrap"
        assert run.vision_bootstrap_count == 3
        proposals = json.loads(run.vision_bootstrap_proposals)
        assert len(proposals) == 3
        assert proposals[0]["number"] == 101
