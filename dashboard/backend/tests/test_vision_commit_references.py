"""End-to-end: committing a vision with attachments uploads them to GitHub
and lists them in docs/vision.md (spec 2026-05-21)."""
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook

from app.database import Base, async_session, engine
from app.main import app
from app.models import Project, VisionChatAttachment, VisionChatSession
from app.services.vision_attachments import store_attachment
from app.services.github_contents import ContentsResult


def _xlsx() -> bytes:
    wb = Workbook()
    wb.active.append(["a"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest_asyncio.fixture
async def clean_db():
    """Create tables before each test; drop them after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_commit_writes_vision_md_and_each_reference(clean_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)

    async with async_session() as db:
        p = Project(repo=f"owner/cr-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess)
        await db.commit()
        att = await store_attachment(
            db, session_id=sess.id, raw=_xlsx(), declared_filename="data.xlsx",
        )
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
        pid = p.id

    write_calls = []

    async def fake_write(repo, path, branch, *, body=None, body_bytes=None, message, current_sha):
        write_calls.append({"path": path, "has_body": body is not None, "has_bytes": body_bytes is not None})
        return "fake-sha-" + path.replace("/", "_")

    async def fake_read(repo, path, branch):
        return ContentsResult(
            sha="fake-vision-sha",
            body="# Vision — owner/x\n",
            html_url="https://github.com/owner/x/blob/main/docs/vision.md",
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(side_effect=fake_write)), \
             patch("app.services.github_contents.read_file", new=AsyncMock(side_effect=fake_read)), \
             patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": True})):
            resp = await c.post(
                f"/api/projects/{pid}/vision",
                json={"vision_doc": {"problem": "p", "users": "", "end_state": "",
                                     "non_goals": "", "principles": "", "horizons": "",
                                     "anti_patterns": ""}},
            )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "data.xlsx" in payload["refs_committed"]
    assert payload["refs_failed"] == []

    paths = [c["path"] for c in write_calls]
    assert "docs/vision.md" in paths
    assert "docs/vision-refs/data.xlsx" in paths

    ref_calls = [c for c in write_calls if c["path"].startswith("docs/vision-refs/")]
    assert all(c["has_bytes"] and not c["has_body"] for c in ref_calls)


@pytest.mark.asyncio
async def test_commit_skips_unsent_attachments(clean_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)

    async with async_session() as db:
        p = Project(repo=f"owner/sk-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess)
        await db.commit()
        # Store attachment but do NOT set sent_at
        await store_attachment(
            db, session_id=sess.id, raw=_xlsx(), declared_filename="data.xlsx",
        )
        await db.commit()
        pid = p.id

    write_calls = []

    async def fake_write(repo, path, branch, *, body=None, body_bytes=None, message, current_sha):
        write_calls.append(path)
        return "sha"

    async def fake_read(repo, path, branch):
        return ContentsResult(sha="sha", body="x", html_url="u")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(side_effect=fake_write)), \
             patch("app.services.github_contents.read_file", new=AsyncMock(side_effect=fake_read)), \
             patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": True})):
            resp = await c.post(
                f"/api/projects/{pid}/vision",
                json={"vision_doc": {"problem": "p", "users": "", "end_state": "",
                                     "non_goals": "", "principles": "", "horizons": "",
                                     "anti_patterns": ""}},
            )

    assert resp.status_code == 200
    assert resp.json()["refs_committed"] == []
    # Only docs/vision.md should have been written (no refs)
    assert write_calls == ["docs/vision.md"]


@pytest.mark.asyncio
async def test_commit_partial_ref_failure_keeps_disk_files(clean_db, tmp_path, monkeypatch):
    """When a ref upload fails, disk files are NOT cleaned up."""
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)

    async with async_session() as db:
        p = Project(repo=f"owner/pf-{uuid.uuid4().hex[:8]}", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess)
        await db.commit()
        att = await store_attachment(
            db, session_id=sess.id, raw=_xlsx(), declared_filename="fail.xlsx",
        )
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
        pid = p.id
        disk_path = att.disk_path
        session_id = sess.id

    async def fake_write_fail(repo, path, branch, *, body=None, body_bytes=None, message, current_sha):
        if "vision-refs" in path:
            raise RuntimeError("simulated GitHub error")
        return "sha-vision"

    async def fake_read(repo, path, branch):
        return ContentsResult(sha="sha-vision", body="x", html_url="u")

    import os

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(side_effect=fake_write_fail)), \
             patch("app.services.github_contents.read_file", new=AsyncMock(side_effect=fake_read)), \
             patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": True})):
            resp = await c.post(
                f"/api/projects/{pid}/vision",
                json={"vision_doc": {"problem": "p", "users": "", "end_state": "",
                                     "non_goals": "", "principles": "", "horizons": "",
                                     "anti_patterns": ""}},
            )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["refs_committed"] == []
    assert len(payload["refs_failed"]) == 1
    assert payload["refs_failed"][0]["filename"] == "fail.xlsx"

    # Disk file must still exist (cleanup skipped on partial failure)
    assert os.path.exists(disk_path), "disk file should not have been cleaned up on partial failure"
