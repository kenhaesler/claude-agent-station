"""Multi-block user message construction from attachments (spec 2026-05-21)."""
import base64
import io
import uuid

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import Project, VisionChatAttachment, VisionChatSession
from app.services.vision_attachments import build_chat_blocks, store_attachment


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["x"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _seed(async_session_factory) -> str:
    async with async_session_factory() as db:
        p = Project(repo=f"owner/blk-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        return sess.id


@pytest.mark.asyncio
async def test_build_blocks_for_text_pdf_image_and_xlsx(
    async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _seed(async_session_factory)
    async with async_session_factory() as db:
        pdf_att = await store_attachment(db, session_id=sid, raw=_PDF, declared_filename="a.pdf")
        png_att = await store_attachment(db, session_id=sid, raw=_PNG, declared_filename="b.png")
        xls_att = await store_attachment(db, session_id=sid, raw=_xlsx(), declared_filename="c.xlsx")
        await db.commit()
        pdf_id, png_id, xls_id = pdf_att.id, png_att.id, xls_att.id

    async with async_session_factory() as db:
        blocks = await build_chat_blocks(
            db,
            user_text="hello",
            attachment_ids=[pdf_id, png_id, xls_id],
        )
        await db.commit()

    assert blocks[0] == {"type": "text", "text": "hello"}

    pdf_block = blocks[1]
    assert pdf_block["type"] == "document"
    assert pdf_block["source"]["type"] == "base64"
    assert pdf_block["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(pdf_block["source"]["data"]) == _PDF

    img_block = blocks[2]
    assert img_block["type"] == "image"
    assert img_block["source"]["type"] == "base64"
    assert img_block["source"]["media_type"] == "image/png"

    xls_block = blocks[3]
    assert xls_block["type"] == "text"
    assert "--- Attached file: c.xlsx" in xls_block["text"]
    assert "Sheet:" in xls_block["text"]


@pytest.mark.asyncio
async def test_build_blocks_marks_sent_at(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    sid = await _seed(async_session_factory)
    async with async_session_factory() as db:
        att = await store_attachment(db, session_id=sid, raw=_PDF, declared_filename="a.pdf")
        await db.commit()
        assert att.sent_at is None
        aid = att.id

    async with async_session_factory() as db:
        await build_chat_blocks(db, user_text="hi", attachment_ids=[aid])
        await db.commit()

    async with async_session_factory() as db:
        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == aid)
        )
        assert result.scalar_one().sent_at is not None
