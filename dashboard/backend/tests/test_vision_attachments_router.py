"""Vision attachment HTTP endpoints (spec 2026-05-21)."""
import io
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, VisionChatAttachment, VisionChatSession


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest_asyncio.fixture
async def project_and_session():
    """Insert a Project + active VisionChatSession; yield (project_id, session_id)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        p = Project(repo=f"owner/test-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess)
        await db.commit()
        yield p.id, sess.id
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def project_only():
    """Insert a Project only (no active session); yield project_id."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        p = Project(repo=f"owner/lazy-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        yield p.id
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_upload_creates_attachment(project_and_session, tmp_path, monkeypatch):
    project_id, _ = project_and_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("data.xlsx", _xlsx(), "application/octet-stream")}
        resp = await ac.post(
            f"/api/projects/{project_id}/vision/chat/attachments", files=files,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "data.xlsx"
    assert body["mime_type"].endswith("spreadsheetml.sheet")
    assert body["size_bytes"] > 0


@pytest.mark.asyncio
async def test_upload_lazy_creates_session(project_only, tmp_path, monkeypatch):
    pid = project_only
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("a.csv", b"x,y\n1,2\n", "text/csv")}
        resp = await ac.post(f"/api/projects/{pid}/vision/chat/attachments", files=files)
    assert resp.status_code == 200, resp.text

    async with async_session() as db:
        result = await db.execute(select(VisionChatSession).where(VisionChatSession.project_id == pid))
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime(project_and_session, tmp_path, monkeypatch):
    project_id, _ = project_and_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("evil.exe", b"MZ\x90\x00\x03 evil binary content here", "application/octet-stream")}
        resp = await ac.post(
            f"/api/projects/{project_id}/vision/chat/attachments", files=files,
        )
    assert resp.status_code == 415
    assert "not a supported" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_rejects_oversize(project_and_session, tmp_path, monkeypatch):
    project_id, _ = project_and_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    big = b"%PDF-1.4\n" + b"x" * (11 * 1024 * 1024)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("big.pdf", big, "application/pdf")}
        resp = await ac.post(
            f"/api/projects/{project_id}/vision/chat/attachments", files=files,
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_delete_unsent_succeeds(project_and_session, tmp_path, monkeypatch):
    project_id, _ = project_and_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("a.csv", b"x\n1\n", "text/csv")}
        up = await ac.post(
            f"/api/projects/{project_id}/vision/chat/attachments", files=files,
        )
        aid = up.json()["id"]
        resp = await ac.delete(
            f"/api/projects/{project_id}/vision/chat/attachments/{aid}",
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_sent_returns_409(project_and_session, tmp_path, monkeypatch):
    project_id, _ = project_and_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("a.csv", b"x\n1\n", "text/csv")}
        up = await ac.post(
            f"/api/projects/{project_id}/vision/chat/attachments", files=files,
        )
        aid = up.json()["id"]

    # Mark the attachment as sent
    async with async_session() as db:
        att = await db.get(VisionChatAttachment, aid)
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(
            f"/api/projects/{project_id}/vision/chat/attachments/{aid}",
        )
    assert resp.status_code == 409
