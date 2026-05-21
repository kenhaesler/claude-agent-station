"""Session resume surfaces attachments; cancel cleans disk (spec 2026-05-21)."""
import io
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, VisionChatSession
from app.services.vision_attachments import store_attachment


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_get_session_includes_pending_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as db:
        p = Project(repo=f"owner/r-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="pending.xlsx")
        await db.commit()
        pid = p.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/projects/{pid}/vision/chat")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pending_attachments"]) == 1
    assert body["pending_attachments"][0]["filename"] == "pending.xlsx"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_delete_session_removes_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as db:
        p = Project(repo=f"owner/c-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="a.xlsx")
        await db.commit()
        upload_dir = tmp_path / sess.id
        assert upload_dir.exists()
        pid = p.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(f"/api/projects/{pid}/vision/chat")

    assert resp.status_code == 204
    assert not upload_dir.exists()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
