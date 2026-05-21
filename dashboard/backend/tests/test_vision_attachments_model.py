"""Tests for the VisionChatAttachment ORM model (spec 2026-05-21)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Project, VisionChatAttachment, VisionChatSession


@pytest.mark.asyncio
async def test_attachment_persists_with_session_fk(async_session_factory):
    async with async_session_factory() as db:
        project = Project(repo=f"org/repo-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(project)
        await db.commit()
        await db.refresh(project)

        session = VisionChatSession(
            id=str(uuid.uuid4()),
            project_id=project.id,
            state="active",
            phase="freeform",
            coverage="{}",
            messages="[]",
        )
        db.add(session)
        await db.commit()

        att = VisionChatAttachment(
            id=str(uuid.uuid4()),
            session_id=session.id,
            filename="foo.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=1234,
            disk_path="/tmp/uploads/foo.xlsx",
            extracted_text="| a | b |\n| 1 | 2 |",
        )
        db.add(att)
        await db.commit()

        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.session_id == session.id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].filename == "foo.xlsx"
        assert rows[0].sent_at is None
        assert rows[0].created_at is not None
