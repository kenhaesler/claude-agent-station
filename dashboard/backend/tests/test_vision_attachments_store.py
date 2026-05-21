"""Storage service for vision attachments (spec 2026-05-21)."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import Project, VisionChatAttachment, VisionChatSession
from app.services.vision_attachments import (
    AttachmentRejected, MAX_FILE_BYTES, MAX_SESSION_BYTES,
    store_attachment, delete_attachment, cleanup_session_dir,
)


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a", "b"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


async def _make_session(async_session_factory) -> str:
    """Helper to insert a Project + active VisionChatSession; return session id."""
    async with async_session_factory() as db:
        p = Project(repo=f"owner/test-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        return sess.id


@pytest.mark.asyncio
async def test_store_writes_disk_and_row(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.vision_attachments._upload_root",
        lambda: tmp_path,
    )
    sid = await _make_session(async_session_factory)
    async with async_session_factory() as db:
        att = await store_attachment(
            db, session_id=sid, raw=_xlsx(), declared_filename="data.xlsx",
        )
        await db.commit()

        # Disk
        assert Path(att.disk_path).exists()
        assert Path(att.disk_path).parent == tmp_path / sid
        assert Path(att.disk_path).read_bytes()

        # Row
        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == att.id)
        )
        row = result.scalar_one()
        assert row.filename == "data.xlsx"
        assert row.extracted_text  # non-native → extracted


@pytest.mark.asyncio
async def test_store_collision_within_session_suffixes(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _make_session(async_session_factory)
    async with async_session_factory() as db:
        a = await store_attachment(db, session_id=sid, raw=_xlsx(), declared_filename="data.xlsx")
        await db.commit()
    async with async_session_factory() as db:
        b = await store_attachment(db, session_id=sid, raw=_xlsx(), declared_filename="data.xlsx")
        await db.commit()
        assert a.filename == "data.xlsx"
        assert b.filename == "data-2.xlsx"


@pytest.mark.asyncio
async def test_store_rejects_oversize_file(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _make_session(async_session_factory)
    big = b"x" * (MAX_FILE_BYTES + 1)
    async with async_session_factory() as db:
        with pytest.raises(AttachmentRejected) as exc:
            await store_attachment(db, session_id=sid, raw=big, declared_filename="big.txt")
        assert "10 MB" in str(exc.value) or "max" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_store_rejects_session_overage(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _make_session(async_session_factory)
    existing_size = 35 * 1024 * 1024
    async with async_session_factory() as db:
        db.add(VisionChatAttachment(
            id=str(uuid.uuid4()), session_id=sid,
            filename="pre.bin", mime_type="application/pdf",
            size_bytes=existing_size, disk_path="/dev/null",
        ))
        await db.commit()
    new_data = b"%PDF-1.4\n" + b"x" * (6 * 1024 * 1024)
    async with async_session_factory() as db:
        with pytest.raises(AttachmentRejected) as exc:
            await store_attachment(db, session_id=sid, raw=new_data, declared_filename="big.pdf")
        assert "session" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_delete_removes_disk_and_row(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _make_session(async_session_factory)
    async with async_session_factory() as db:
        att = await store_attachment(db, session_id=sid, raw=_xlsx(), declared_filename="x.xlsx")
        await db.commit()
        path = Path(att.disk_path)
        aid = att.id
    async with async_session_factory() as db:
        await delete_attachment(db, attachment_id=aid)
        await db.commit()
    assert not path.exists()
    async with async_session_factory() as db:
        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == aid)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_refuses_if_already_sent(async_session_factory, tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _make_session(async_session_factory)
    async with async_session_factory() as db:
        att = await store_attachment(db, session_id=sid, raw=_xlsx(), declared_filename="x.xlsx")
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
        aid = att.id
    async with async_session_factory() as db:
        with pytest.raises(AttachmentRejected) as exc:
            await delete_attachment(db, attachment_id=aid)
        assert "sent" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_cleanup_session_dir_removes_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = uuid.uuid4().hex
    d = tmp_path / sid
    d.mkdir(parents=True)
    (d / "a.txt").write_text("hi")
    cleanup_session_dir(sid)
    assert not d.exists()
