# Vision Reference Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users attach reference files (Excel, PDF, images, CSV, docx, txt, md) during the Vision chat. PDFs and images go to Claude as native blocks; Excel/CSV/docx are extracted to text server-side. On commit, all files are persisted to the target project's repo under `docs/vision-refs/` and listed in a `## References` section of `docs/vision.md`.

**Architecture:** A new `vision_chat_attachments` table tracks uploads per chat session. Files live on disk under `VISION_UPLOAD_DIR/<session_id>/`. New backend endpoints handle upload, delete, and (extended) chat-turn + commit flows. Frontend gets a paperclip + dropzone in `VisionChat.svelte`, chip strip above the input, and inline attachment chips on past messages. Spec: `docs/superpowers/specs/2026-05-21-vision-reference-files-design.md`.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Alembic / openpyxl / python-docx / python-magic / Anthropic Python SDK; Svelte 5 + Vite + TailwindCSS; SQLite (dev/prod default) and Postgres (compatible via Alembic).

---

## Pre-flight

### Task 0: Read the spec and set up the worktree

**Files:**
- Read: `docs/superpowers/specs/2026-05-21-vision-reference-files-design.md`
- Read: `dashboard/backend/app/routers/vision.py`
- Read: `dashboard/backend/app/services/vision_chat.py`
- Read: `dashboard/backend/app/services/vision_render.py`
- Read: `dashboard/backend/app/services/github_contents.py`
- Read: `dashboard/backend/app/services/vision_cleanup.py`
- Read: `dashboard/backend/app/models.py` (look for `VisionChatSession` ~ line 500)
- Read: `dashboard/backend/app/schemas.py` (look for `VisionDoc`, `VisionChatSessionOut` ~ line 940+)
- Read: `dashboard/frontend/src/components/vision/VisionChat.svelte`
- Read: `dashboard/frontend/src/lib/api.ts` (vision helpers ~ line 568)
- Read: `dashboard/frontend/src/lib/types.ts` (look for `VisionMessage`, `VisionChatSession`)
- Read: `dashboard/frontend/src/lib/vision-sse.ts`

- [ ] **Step 1: Confirm you're on the spec's feature branch**

```bash
git branch --show-current
```

Expected: `feature/vision-reference-files-spec` (the spec branch). If not, check out the spec and continue from a sibling branch.

- [ ] **Step 2: Verify backend tests pass on a clean tree**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_router.py tests/test_vision_chat_service.py tests/test_vision_render.py -q
```

Expected: PASS for all collected tests. If any fail on `main`, surface that — we don't want to be debugging unrelated failures later.

- [ ] **Step 3: Verify frontend tests pass on a clean tree**

```bash
cd dashboard/frontend && npm run test -- --run src/lib/vision-sse.test.ts
```

Expected: PASS.

---

## Phase 1 — Backend foundation

### Task 1: Add the `vision_chat_attachments` ORM model

**Files:**
- Modify: `dashboard/backend/app/models.py` — append after `VisionChatSession` (~line 518)
- Test: `dashboard/backend/tests/test_vision_attachments_model.py` (create)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_attachments_model.py`:

```python
"""Tests for the VisionChatAttachment ORM model (spec 2026-05-21)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import VisionChatAttachment, VisionChatSession


@pytest.mark.asyncio
async def test_attachment_persists_with_session_fk(async_session_factory):
    async with async_session_factory() as db:
        session = VisionChatSession(
            id=str(uuid.uuid4()),
            project_id=1,
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
```

- [ ] **Step 2: Run test, confirm it fails on the missing model**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_model.py -v
```

Expected: FAIL with `ImportError: cannot import name 'VisionChatAttachment'`.

- [ ] **Step 3: Add the model**

Append to `dashboard/backend/app/models.py` after the existing `VisionChatSession` class (after line 518):

```python
class VisionChatAttachment(Base):
    """Reference file attached to a vision chat session.

    Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.

    Files live on disk under VISION_UPLOAD_DIR/<session_id>/; this row is the
    metadata + extraction cache. ``sent_at`` is set when the attachment is
    first included in a chat turn — once set, DELETE is refused (the file is
    part of the conversation history).
    """
    __tablename__ = "vision_chat_attachments"

    id = Column(Text, primary_key=True)  # UUID
    session_id = Column(
        Text,
        ForeignKey("vision_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    disk_path = Column(Text, nullable=False)
    extracted_text = Column(Text, nullable=True)  # populated for non-native types
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_model.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/tests/test_vision_attachments_model.py
git commit -m "feat(vision): add VisionChatAttachment ORM model"
```

---

### Task 2: Alembic migration for `vision_chat_attachments`

**Files:**
- Create: `dashboard/backend/alembic/versions/0005_vision_chat_attachments.py`
- Test: `dashboard/backend/tests/test_alembic_migrations.py` — should already pick up the new revision

- [ ] **Step 1: Write the failing migration-shape test**

Append to `dashboard/backend/tests/test_alembic_migrations.py` (or create a new `test_vision_attachments_migration.py`):

```python
def test_revision_0005_creates_attachments_table(alembic_engine):
    """0005 must add vision_chat_attachments with the spec'd columns + index."""
    from sqlalchemy import inspect
    insp = inspect(alembic_engine)
    assert "vision_chat_attachments" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("vision_chat_attachments")}
    assert cols >= {
        "id", "session_id", "filename", "mime_type", "size_bytes",
        "disk_path", "extracted_text", "sent_at", "created_at",
    }
    idx_names = {ix["name"] for ix in insp.get_indexes("vision_chat_attachments")}
    assert "ix_vision_chat_attachments_session_id" in idx_names
```

If the existing test file uses a different fixture name, mirror that pattern — read `tests/test_alembic_migrations.py` for the fixture name before writing the assertion.

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_alembic_migrations.py -v -k revision_0005
```

Expected: FAIL (table does not exist).

- [ ] **Step 3: Create the migration**

Create `dashboard/backend/alembic/versions/0005_vision_chat_attachments.py`:

```python
"""Add vision_chat_attachments table.

Revision ID: 0005_vision_chat_attachments
Revises: 0004_run_kind_parent
Create Date: 2026-05-21

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.
Stores per-session reference-file metadata + extraction cache.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_vision_chat_attachments"
down_revision = "0004_run_kind_parent"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _index_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    if not _table_exists("vision_chat_attachments"):
        op.create_table(
            "vision_chat_attachments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "session_id",
                sa.Text(),
                sa.ForeignKey("vision_chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("mime_type", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("disk_path", sa.Text(), nullable=False),
            sa.Column("extracted_text", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _index_exists("vision_chat_attachments", "ix_vision_chat_attachments_session_id"):
        op.create_index(
            "ix_vision_chat_attachments_session_id",
            "vision_chat_attachments",
            ["session_id"],
        )


def downgrade() -> None:
    if _index_exists("vision_chat_attachments", "ix_vision_chat_attachments_session_id"):
        op.drop_index(
            "ix_vision_chat_attachments_session_id", "vision_chat_attachments",
        )
    if _table_exists("vision_chat_attachments"):
        op.drop_table("vision_chat_attachments")
```

- [ ] **Step 4: Run the migration test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_alembic_migrations.py -v
```

Expected: PASS.

- [ ] **Step 5: Re-run the model test to confirm migrations + model agree**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_model.py tests/test_alembic_migrations.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/alembic/versions/0005_vision_chat_attachments.py dashboard/backend/tests/test_alembic_migrations.py
git commit -m "feat(vision): alembic 0005 — vision_chat_attachments table"
```

---

### Task 3: Add dependencies + config

**Files:**
- Modify: `dashboard/backend/requirements.txt`
- Modify: `dashboard/backend/app/config.py` (find with `grep -n "class Settings" dashboard/backend/app/config.py`)
- Test: `dashboard/backend/tests/test_config.py` — extend if it covers Settings; otherwise create `test_vision_upload_settings.py`

- [ ] **Step 1: Write the failing settings test**

Create `dashboard/backend/tests/test_vision_upload_settings.py`:

```python
"""VISION_UPLOAD_DIR config (spec 2026-05-21)."""
import os
from pathlib import Path

from app.config import get_settings


def test_vision_upload_dir_default():
    s = get_settings()
    assert Path(s.vision_upload_dir) == Path("/var/lib/claude-agent-station/vision-chat-uploads")


def test_vision_upload_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()  # if @lru_cache; otherwise no-op
    s = get_settings()
    assert Path(s.vision_upload_dir) == tmp_path / "uploads"
```

If `get_settings` is not `lru_cache`-d, drop the `cache_clear` line. Inspect `app/config.py` first.

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_upload_settings.py -v
```

Expected: FAIL (AttributeError: `vision_upload_dir`).

- [ ] **Step 3: Add the setting**

In `dashboard/backend/app/config.py`, locate the `Settings` Pydantic model and add:

```python
    vision_upload_dir: str = "/var/lib/claude-agent-station/vision-chat-uploads"
```

(Maintain the existing field naming conventions — if the project uses `Field(..., env="VISION_UPLOAD_DIR")`, mirror that.)

- [ ] **Step 4: Add Python dependencies**

Append to `dashboard/backend/requirements.txt`:

```
openpyxl>=3.1
python-docx>=1.1
python-magic>=0.4.27
```

Install locally:

```bash
cd dashboard/backend && pip install openpyxl 'python-docx>=1.1' python-magic
```

If `python-magic` import fails at runtime due to missing `libmagic`, note it in `docs/deployment.md` (Task 16) — for now, dev systems typically have it. Verify import works:

```bash
cd dashboard/backend && python -c "import openpyxl, docx, magic; print('ok')"
```

Expected: `ok`. If `magic` fails, install libmagic via system package manager (`dnf install file-libs file-devel` on Rocky/RHEL; `brew install libmagic` on macOS).

- [ ] **Step 5: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_upload_settings.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/requirements.txt dashboard/backend/app/config.py dashboard/backend/tests/test_vision_upload_settings.py
git commit -m "feat(vision): VISION_UPLOAD_DIR setting + extraction deps"
```

---

### Task 4: Filename sanitisation utility

**Files:**
- Create: `dashboard/backend/app/services/vision_attachments.py`
- Test: `dashboard/backend/tests/test_vision_attachments_sanitize.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_attachments_sanitize.py`:

```python
"""Filename sanitisation for vision attachments (spec 2026-05-21)."""
import pytest

from app.services.vision_attachments import sanitize_filename


@pytest.mark.parametrize("raw,expected", [
    ("simple.xlsx", "simple.xlsx"),
    ("with spaces.pdf", "with spaces.pdf"),
    ("bad<chars>.csv", "badchars.csv"),
    ("bad|chars?.xlsx", "badchars.xlsx"),
    ('quotes"and:colons.pdf', "quotesandcolons.pdf"),
    ("../escape.md", "escape.md"),
    ("....many.dots.txt", "many.dots.txt"),
    ("/abs/path/foo.xlsx", "foo.xlsx"),
    ("C:\\win\\foo.xlsx", "foo.xlsx"),
])
def test_sanitize_strips_forbidden_chars_and_paths(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_filename("")
    with pytest.raises(ValueError):
        sanitize_filename("....")


def test_sanitize_caps_length():
    long = "a" * 300 + ".xlsx"
    out = sanitize_filename(long)
    assert len(out) <= 255
    assert out.endswith(".xlsx")
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_sanitize.py -v
```

Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

Create `dashboard/backend/app/services/vision_attachments.py`:

```python
"""Vision attachment helpers: sanitise filenames, sniff MIME, extract text.

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.
"""
from __future__ import annotations

import os
import re

# Anthropic's forbidden-character set plus path separators
_FORBIDDEN = re.compile(r'[<>:"|?*\\/]')


def sanitize_filename(raw: str) -> str:
    """Strip path components and forbidden characters; cap length at 255.

    Raises ValueError if nothing usable remains.
    """
    # Take only the basename — strips both Unix and Windows path separators
    base = os.path.basename(raw.replace("\\", "/"))
    cleaned = _FORBIDDEN.sub("", base)
    # Strip leading dots (no `.foo` or `..foo`) and whitespace
    cleaned = cleaned.lstrip(". ").rstrip(" ")
    if not cleaned:
        raise ValueError("filename empty after sanitisation")
    if len(cleaned) > 255:
        # Try to preserve extension
        root, ext = os.path.splitext(cleaned)
        keep = 255 - len(ext)
        cleaned = root[:keep] + ext
    return cleaned
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_sanitize.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_attachments.py dashboard/backend/tests/test_vision_attachments_sanitize.py
git commit -m "feat(vision): filename sanitiser for attachments"
```

---

### Task 5: MIME sniffing + allowlist

**Files:**
- Modify: `dashboard/backend/app/services/vision_attachments.py`
- Test: `dashboard/backend/tests/test_vision_attachments_mime.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_attachments_mime.py`:

```python
"""MIME sniffing + allowlist for vision attachments (spec 2026-05-21)."""
import io

import pytest

from app.services.vision_attachments import (
    AttachmentRejected, ALLOWED_MIMES, sniff_and_validate_mime,
)


def _pdf_bytes() -> bytes:
    # Minimal valid-enough PDF header for libmagic
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_sniff_accepts_pdf():
    mime = sniff_and_validate_mime(_pdf_bytes(), declared_filename="x.pdf")
    assert mime == "application/pdf"


def test_sniff_accepts_png():
    mime = sniff_and_validate_mime(_png_bytes(), declared_filename="x.png")
    assert mime == "image/png"


def test_sniff_rejects_unknown_type():
    # Random binary — libmagic will say application/octet-stream
    with pytest.raises(AttachmentRejected) as exc:
        sniff_and_validate_mime(b"\x00\x01\x02not a real file", declared_filename="x.bin")
    assert "not a supported" in str(exc.value).lower()


def test_allowed_mimes_contains_expected_set():
    expected = {
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "text/plain", "text/markdown", "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert expected <= ALLOWED_MIMES
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_mime.py -v
```

Expected: FAIL (`AttachmentRejected` / `sniff_and_validate_mime` not defined).

- [ ] **Step 3: Implement**

Append to `dashboard/backend/app/services/vision_attachments.py`:

```python
import magic


class AttachmentRejected(Exception):
    """Raised when an upload fails validation (size, MIME, or sanitisation)."""


ALLOWED_MIMES = frozenset({
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "text/plain", "text/markdown", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
})

# Extension → expected MIME, used to normalise libmagic quirks (e.g. csv often
# detected as text/plain). The extension is advisory only; the sniffed type
# wins for security-sensitive checks like rejecting zips disguised as docx.
_EXT_HINTS: dict[str, str] = {
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def sniff_and_validate_mime(data: bytes, *, declared_filename: str) -> str:
    """Sniff the MIME of *data* with libmagic; return it iff in allowlist.

    libmagic often classifies csv/md as text/plain. We promote based on
    extension only when the sniffed type is text/plain. Raises
    :class:`AttachmentRejected` if the resulting MIME is not allowed.
    """
    sniffed = magic.from_buffer(data, mime=True)

    if sniffed == "text/plain":
        ext = ""
        i = declared_filename.rfind(".")
        if i >= 0:
            ext = declared_filename[i:].lower()
        hinted = _EXT_HINTS.get(ext)
        if hinted:
            sniffed = hinted

    if sniffed not in ALLOWED_MIMES:
        raise AttachmentRejected(
            f"{declared_filename} ({sniffed}) is not a supported reference type. "
            "Supported: PDF, images (png/jpeg/gif/webp), Excel (xlsx), "
            "Word (docx), CSV, txt, md."
        )
    return sniffed
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_mime.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_attachments.py dashboard/backend/tests/test_vision_attachments_mime.py
git commit -m "feat(vision): MIME sniff + allowlist for attachments"
```

---

### Task 6: Text extraction for non-native types

**Files:**
- Modify: `dashboard/backend/app/services/vision_attachments.py`
- Test: `dashboard/backend/tests/test_vision_attachments_extract.py`
- Test fixtures: `dashboard/backend/tests/fixtures/vision_refs/` (create)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_attachments_extract.py`:

```python
"""Text extraction for non-native vision attachment types (spec 2026-05-21)."""
import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.vision_attachments import extract_text, EXTRACTION_MAX_BYTES


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "qty", "price"])
    ws.append(["apple", 3, 0.5])
    ws.append(["banana", 5, 0.25])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv_bytes() -> bytes:
    return b"name,qty\napple,3\nbanana,5\n"


def _docx_bytes() -> bytes:
    from docx import Document
    doc = Document()
    doc.add_paragraph("Hello world.")
    doc.add_paragraph("Second paragraph.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_xlsx_renders_markdown_table():
    text = extract_text(
        _xlsx_bytes(),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert "Sheet1" in text
    assert "| name | qty | price |" in text
    assert "| apple | 3 | 0.5 |" in text


def test_extract_csv_passes_through():
    text = extract_text(_csv_bytes(), mime="text/csv")
    assert "apple,3" in text
    assert "banana,5" in text


def test_extract_docx_paragraphs():
    text = extract_text(_docx_bytes(), mime=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ))
    assert "Hello world." in text
    assert "Second paragraph." in text


def test_extract_returns_none_for_native_types():
    # PDFs/images are sent natively to Claude — no extraction needed.
    assert extract_text(b"\x89PNG\r\n", mime="image/png") is None
    assert extract_text(b"%PDF-1.4", mime="application/pdf") is None


def test_extract_truncates_large_output():
    # Build an xlsx with enough rows to blow past the cap
    wb = Workbook()
    ws = wb.active
    ws.append(["col"])
    for i in range(50_000):
        ws.append([f"row-{i}-padding-padding-padding"])
    buf = io.BytesIO()
    wb.save(buf)

    text = extract_text(
        buf.getvalue(),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert text is not None
    assert len(text) <= EXTRACTION_MAX_BYTES + 200  # cap + marker slack
    assert "[truncated" in text
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_extract.py -v
```

Expected: FAIL (`extract_text` not defined).

- [ ] **Step 3: Implement**

Append to `dashboard/backend/app/services/vision_attachments.py`:

```python
import io
import csv as _csv

from openpyxl import load_workbook

EXTRACTION_MAX_BYTES = 200_000  # 200 KB cap on per-file extracted text


def _truncate(text: str) -> str:
    if len(text.encode("utf-8")) <= EXTRACTION_MAX_BYTES:
        return text
    # Truncate to bytes-safe boundary
    encoded = text.encode("utf-8")[:EXTRACTION_MAX_BYTES]
    truncated = encoded.decode("utf-8", errors="ignore")
    remaining = len(text.encode("utf-8")) - len(encoded)
    return truncated + f"\n\n[truncated — {remaining} more bytes]"


def _xlsx_to_markdown(data: bytes) -> str:
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"### Sheet: {ws.title}\n")
        first_row = True
        for row in ws.iter_rows(values_only=True):
            if all(c is None for c in row):
                continue
            cells = ["" if c is None else str(c) for c in row]
            out.append("| " + " | ".join(cells) + " |")
            if first_row:
                out.append("| " + " | ".join("---" for _ in cells) + " |")
                first_row = False
        out.append("")  # blank line between sheets
    return "\n".join(out)


def _csv_to_text(data: bytes) -> str:
    # Pass through; csv.reader normalises line endings but we want the file
    # as-is for the model. Decode utf-8 with replacement to survive odd bytes.
    return data.decode("utf-8", errors="replace")


def _docx_to_text(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


# MIME types that we extract server-side (the rest go to Claude natively)
_EXTRACTORS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _xlsx_to_markdown,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _docx_to_text,
    "text/csv": _csv_to_text,
    "text/markdown": _csv_to_text,  # pass-through (markdown is already text)
    # text/plain is also a passthrough but Claude takes it natively as a document block.
}


def extract_text(data: bytes, *, mime: str) -> str | None:
    """Return extracted text for non-native types, or None for native types.

    Output is capped at EXTRACTION_MAX_BYTES; oversize results get a
    ``[truncated — N more bytes]`` suffix.
    """
    extractor = _EXTRACTORS.get(mime)
    if extractor is None:
        return None
    raw = extractor(data)
    return _truncate(raw)
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_extract.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_attachments.py dashboard/backend/tests/test_vision_attachments_extract.py
git commit -m "feat(vision): server-side text extraction for xlsx/csv/docx"
```

---

### Task 7: Storage service (disk I/O + DB row creation)

**Files:**
- Modify: `dashboard/backend/app/services/vision_attachments.py` — add `store_attachment`, `delete_attachment`, `cleanup_session_dir`
- Test: `dashboard/backend/tests/test_vision_attachments_store.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_attachments_store.py`:

```python
"""Storage service for vision attachments (spec 2026-05-21)."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import VisionChatAttachment, VisionChatSession
from app.services.vision_attachments import (
    AttachmentRejected, MAX_FILE_BYTES, MAX_SESSION_BYTES,
    store_attachment, delete_attachment, cleanup_session_dir,
)


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a", "b"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest.fixture
async def session_row(async_session_factory):
    async with async_session_factory() as db:
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=1, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        return sess.id


@pytest.mark.asyncio
async def test_store_writes_disk_and_row(async_session_factory, session_row, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.vision_attachments._upload_root",
        lambda: tmp_path,
    )
    async with async_session_factory() as db:
        att = await store_attachment(
            db, session_id=session_row, raw=_xlsx(),
            declared_filename="data.xlsx",
        )
        await db.commit()

        # Disk
        assert Path(att.disk_path).exists()
        assert Path(att.disk_path).parent == tmp_path / session_row
        assert Path(att.disk_path).read_bytes()

        # Row
        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == att.id)
        )
        row = result.scalar_one()
        assert row.filename == "data.xlsx"
        assert row.extracted_text  # non-native → extracted


@pytest.mark.asyncio
async def test_store_collision_within_session_suffixes(async_session_factory, session_row, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        a = await store_attachment(db, session_id=session_row, raw=_xlsx(), declared_filename="data.xlsx")
        b = await store_attachment(db, session_id=session_row, raw=_xlsx(), declared_filename="data.xlsx")
        await db.commit()
        assert a.filename == "data.xlsx"
        assert b.filename == "data-2.xlsx"


@pytest.mark.asyncio
async def test_store_rejects_oversize_file(async_session_factory, session_row, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    big = b"x" * (MAX_FILE_BYTES + 1)
    async with async_session_factory() as db:
        with pytest.raises(AttachmentRejected) as exc:
            await store_attachment(db, session_id=session_row, raw=big, declared_filename="big.txt")
        assert "10 MB" in str(exc.value) or "max" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_store_rejects_session_overage(async_session_factory, session_row, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    # Pre-existing 35 MB
    existing_size = 35 * 1024 * 1024
    async with async_session_factory() as db:
        db.add(VisionChatAttachment(
            id=str(uuid.uuid4()), session_id=session_row,
            filename="pre.bin", mime_type="application/pdf",
            size_bytes=existing_size, disk_path="/dev/null",
        ))
        await db.commit()
    # New 6 MB file pushes over 40 MB cap
    new_data = b"%PDF-1.4\n" + b"x" * (6 * 1024 * 1024)
    async with async_session_factory() as db:
        with pytest.raises(AttachmentRejected) as exc:
            await store_attachment(db, session_id=session_row, raw=new_data, declared_filename="big.pdf")
        assert "session" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_delete_removes_disk_and_row(async_session_factory, session_row, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        att = await store_attachment(db, session_id=session_row, raw=_xlsx(), declared_filename="x.xlsx")
        await db.commit()
        path = Path(att.disk_path)
        await delete_attachment(db, attachment_id=att.id)
        await db.commit()
        assert not path.exists()
        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == att.id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_refuses_if_already_sent(async_session_factory, session_row, tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        att = await store_attachment(db, session_id=session_row, raw=_xlsx(), declared_filename="x.xlsx")
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
        with pytest.raises(AttachmentRejected) as exc:
            await delete_attachment(db, attachment_id=att.id)
        assert "sent" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_cleanup_session_dir_removes_files(tmp_path, session_row, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    d = tmp_path / session_row
    d.mkdir(parents=True)
    (d / "a.txt").write_text("hi")
    cleanup_session_dir(session_row)
    assert not d.exists()
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_store.py -v
```

Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement**

Append to `dashboard/backend/app/services/vision_attachments.py`:

```python
import shutil
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import VisionChatAttachment

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SESSION_BYTES = 40 * 1024 * 1024


def _upload_root() -> Path:
    """Return the configured upload root, creating it if missing."""
    root = Path(get_settings().vision_upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(session_id: str) -> Path:
    d = _upload_root() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique_in_session(db_filenames: set[str], filename: str) -> str:
    if filename not in db_filenames:
        return filename
    root, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
    suffix_ext = f".{ext}" if ext else ""
    for n in range(2, 1000):
        candidate = f"{root}-{n}{suffix_ext}"
        if candidate not in db_filenames:
            return candidate
    raise AttachmentRejected("too many duplicate filenames in session")


async def store_attachment(
    db: AsyncSession,
    *,
    session_id: str,
    raw: bytes,
    declared_filename: str,
) -> VisionChatAttachment:
    """Validate, sniff, extract, write to disk, insert row. Commit is caller's job."""
    if len(raw) > MAX_FILE_BYTES:
        raise AttachmentRejected(
            f"{declared_filename} is {len(raw) // (1024 * 1024)} MB — max 10 MB per file."
        )

    # Per-session total cap
    result = await db.execute(
        select(func.coalesce(func.sum(VisionChatAttachment.size_bytes), 0))
        .where(VisionChatAttachment.session_id == session_id)
    )
    current_total = int(result.scalar_one())
    if current_total + len(raw) > MAX_SESSION_BYTES:
        raise AttachmentRejected(
            "Adding this would exceed the 40 MB per-session attachment limit."
        )

    safe_name = sanitize_filename(declared_filename)
    mime = sniff_and_validate_mime(raw, declared_filename=safe_name)

    # Collision handling: look up existing filenames for this session
    existing = await db.execute(
        select(VisionChatAttachment.filename).where(
            VisionChatAttachment.session_id == session_id
        )
    )
    used = {row[0] for row in existing.all()}
    final_name = _unique_in_session(used, safe_name)

    # Disk: <upload_root>/<session_id>/<uuid>-<filename>
    disk_id = _uuid.uuid4().hex
    disk_path = _session_dir(session_id) / f"{disk_id}-{final_name}"
    disk_path.write_bytes(raw)

    extracted = extract_text(raw, mime=mime)

    att = VisionChatAttachment(
        id=str(_uuid.uuid4()),
        session_id=session_id,
        filename=final_name,
        mime_type=mime,
        size_bytes=len(raw),
        disk_path=str(disk_path),
        extracted_text=extracted,
    )
    db.add(att)
    return att


async def delete_attachment(db: AsyncSession, *, attachment_id: str) -> None:
    """Delete an attachment IFF it has not yet been sent in a chat turn."""
    att = await db.get(VisionChatAttachment, attachment_id)
    if att is None:
        raise AttachmentRejected("attachment not found")
    if att.sent_at is not None:
        raise AttachmentRejected("cannot delete an attachment already sent in a chat turn")
    path = Path(att.disk_path)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass  # log-and-continue on disk weirdness
    await db.delete(att)


def cleanup_session_dir(session_id: str) -> None:
    """Remove the session's upload dir on disk (no-op if absent)."""
    d = _upload_root() / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_store.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_attachments.py dashboard/backend/tests/test_vision_attachments_store.py
git commit -m "feat(vision): attachment storage service (disk + DB + caps)"
```

---

## Phase 2 — Backend HTTP endpoints

### Task 8: POST and DELETE attachment endpoints

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py`
- Modify: `dashboard/backend/app/schemas.py` (add `VisionAttachmentOut`)
- Test: `dashboard/backend/tests/test_vision_attachments_router.py`

- [ ] **Step 1: Add schema**

Add to `dashboard/backend/app/schemas.py` after `VisionChatSessionOut` (around line 998):

```python
class VisionAttachmentOut(BaseModel):
    """Response for POST /api/projects/{id}/vision/chat/attachments and on session resume."""
    id: str
    filename: str
    mime_type: str
    size_bytes: int
```

- [ ] **Step 2: Write the failing endpoint tests**

Create `dashboard/backend/tests/test_vision_attachments_router.py`. Read `tests/test_vision_router.py` first to mirror its `httpx_client` / `async_client` fixture name.

```python
"""Vision attachment HTTP endpoints (spec 2026-05-21)."""
import io
import uuid

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import Project, VisionChatAttachment, VisionChatSession


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest.fixture
async def project_with_active_session(async_session_factory):
    async with async_session_factory() as db:
        p = Project(repo="owner/example", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        return p.id, sess.id


@pytest.mark.asyncio
async def test_upload_creates_attachment(
    async_client, async_session_factory, project_with_active_session, tmp_path, monkeypatch
):
    project_id, session_id = project_with_active_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    files = {"file": ("data.xlsx", _xlsx(), "application/octet-stream")}
    resp = await async_client.post(
        f"/api/projects/{project_id}/vision/chat/attachments", files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "data.xlsx"
    assert body["mime_type"].endswith("spreadsheetml.sheet")
    assert body["size_bytes"] > 0


@pytest.mark.asyncio
async def test_upload_lazy_creates_session(
    async_client, async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        p = Project(repo="owner/example2", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        pid = p.id

    files = {"file": ("a.csv", b"x,y\n1,2\n", "text/csv")}
    resp = await async_client.post(f"/api/projects/{pid}/vision/chat/attachments", files=files)
    assert resp.status_code == 200, resp.text
    # Session must now exist
    async with async_session_factory() as db:
        result = await db.execute(select(VisionChatSession).where(VisionChatSession.project_id == pid))
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime(
    async_client, project_with_active_session, tmp_path, monkeypatch
):
    project_id, _ = project_with_active_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    files = {"file": ("evil.exe", b"MZ\x90\x00\x03", "application/octet-stream")}
    resp = await async_client.post(
        f"/api/projects/{project_id}/vision/chat/attachments", files=files,
    )
    assert resp.status_code == 415
    assert "not a supported" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_rejects_oversize(
    async_client, project_with_active_session, tmp_path, monkeypatch
):
    project_id, _ = project_with_active_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    big = b"%PDF-1.4\n" + b"x" * (11 * 1024 * 1024)
    files = {"file": ("big.pdf", big, "application/pdf")}
    resp = await async_client.post(
        f"/api/projects/{project_id}/vision/chat/attachments", files=files,
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_delete_unsent_succeeds(
    async_client, async_session_factory, project_with_active_session, tmp_path, monkeypatch
):
    project_id, session_id = project_with_active_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    files = {"file": ("a.csv", b"x\n1\n", "text/csv")}
    up = await async_client.post(
        f"/api/projects/{project_id}/vision/chat/attachments", files=files,
    )
    aid = up.json()["id"]
    resp = await async_client.delete(
        f"/api/projects/{project_id}/vision/chat/attachments/{aid}",
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_sent_returns_409(
    async_client, async_session_factory, project_with_active_session, tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    project_id, session_id = project_with_active_session
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    files = {"file": ("a.csv", b"x\n1\n", "text/csv")}
    up = await async_client.post(
        f"/api/projects/{project_id}/vision/chat/attachments", files=files,
    )
    aid = up.json()["id"]
    async with async_session_factory() as db:
        att = await db.get(VisionChatAttachment, aid)
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
    resp = await async_client.delete(
        f"/api/projects/{project_id}/vision/chat/attachments/{aid}",
    )
    assert resp.status_code == 409
```

- [ ] **Step 3: Run tests, confirm they fail**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_router.py -v
```

Expected: 404 (endpoints not implemented).

- [ ] **Step 4: Implement the endpoints**

Add to `dashboard/backend/app/routers/vision.py` after the existing `delete_chat_session` (around line 260):

```python
from fastapi import UploadFile, File
from app.schemas import VisionAttachmentOut
from app.services import vision_attachments as va
from app.services.vision_chat import create_session, SessionAlreadyActive


@router.post(
    "/{project_id}/vision/chat/attachments",
    response_model=VisionAttachmentOut,
)
async def upload_chat_attachment(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> VisionAttachmentOut:
    """Upload a reference file for the active vision chat session.

    Lazily creates an active session if one doesn't exist (mirrors chat_turn).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    session = await get_active_session(db, project_id)
    if session is None:
        try:
            session = await create_session(db, project_id)
        except SessionAlreadyActive as exc:
            # race: another caller created it; re-fetch
            session = await db.get(VisionChatSession, exc.existing_session_id)

    raw = await file.read()
    try:
        att = await va.store_attachment(
            db, session_id=session.id, raw=raw,
            declared_filename=file.filename or "upload.bin",
        )
    except va.AttachmentRejected as exc:
        msg = str(exc)
        # Map specific errors to specific HTTP codes
        if "max 10 MB" in msg or "session" in msg.lower() and "limit" in msg.lower():
            raise HTTPException(status_code=413, detail=msg)
        if "not a supported" in msg.lower():
            raise HTTPException(status_code=415, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    await db.commit()
    return VisionAttachmentOut(
        id=att.id, filename=att.filename,
        mime_type=att.mime_type, size_bytes=att.size_bytes,
    )


@router.delete(
    "/{project_id}/vision/chat/attachments/{attachment_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_chat_attachment(
    project_id: int,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an attachment before it's been sent in a chat turn."""
    from app.models import VisionChatAttachment
    att = await db.get(VisionChatAttachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    # Verify project ownership through session
    sess = await db.get(VisionChatSession, att.session_id)
    if sess is None or sess.project_id != project_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    try:
        await va.delete_attachment(db, attachment_id=attachment_id)
    except va.AttachmentRejected as exc:
        msg = str(exc).lower()
        if "already sent" in msg:
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
```

- [ ] **Step 5: Run tests, confirm they pass**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_attachments_router.py -v
```

Expected: PASS (all 6).

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_vision_attachments_router.py
git commit -m "feat(vision): upload/delete attachment endpoints"
```

---

### Task 9: Build multi-block chat-turn message from attachments

**Files:**
- Modify: `dashboard/backend/app/services/vision_chat.py` — extend `run_chat_turn`
- Modify: `dashboard/backend/app/services/vision_attachments.py` — add `build_chat_blocks`
- Modify: `dashboard/backend/app/routers/vision.py` — thread `attachment_ids` through
- Modify: `dashboard/backend/app/schemas.py` — extend `VisionChatTurnIn`
- Test: `dashboard/backend/tests/test_vision_chat_blocks.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_chat_blocks.py`:

```python
"""Multi-block user message construction from attachments (spec 2026-05-21)."""
import base64
import io
import uuid
from datetime import datetime, timezone

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import VisionChatAttachment, VisionChatSession
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


@pytest.mark.asyncio
async def test_build_blocks_for_text_pdf_image_and_xlsx(
    async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=1, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        pdf_att = await store_attachment(db, session_id=sess.id, raw=_PDF, declared_filename="a.pdf")
        png_att = await store_attachment(db, session_id=sess.id, raw=_PNG, declared_filename="b.png")
        xls_att = await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="c.xlsx")
        await db.commit()

        blocks = await build_chat_blocks(
            db,
            user_text="hello",
            attachment_ids=[pdf_att.id, png_att.id, xls_att.id],
        )

    # First block: the user's typed text
    assert blocks[0] == {"type": "text", "text": "hello"}

    # PDF → document block, base64 source
    pdf_block = blocks[1]
    assert pdf_block["type"] == "document"
    assert pdf_block["source"]["type"] == "base64"
    assert pdf_block["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(pdf_block["source"]["data"]) == _PDF

    # PNG → image block
    img_block = blocks[2]
    assert img_block["type"] == "image"
    assert img_block["source"]["type"] == "base64"
    assert img_block["source"]["media_type"] == "image/png"

    # xlsx → text block with extraction prefix
    xls_block = blocks[3]
    assert xls_block["type"] == "text"
    assert "--- Attached file: c.xlsx" in xls_block["text"]
    assert "Sheet:" in xls_block["text"]


@pytest.mark.asyncio
async def test_build_blocks_marks_sent_at(async_session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=1, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        att = await store_attachment(db, session_id=sess.id, raw=_PDF, declared_filename="a.pdf")
        await db.commit()
        assert att.sent_at is None

        await build_chat_blocks(db, user_text="hi", attachment_ids=[att.id])
        await db.commit()

        result = await db.execute(
            select(VisionChatAttachment).where(VisionChatAttachment.id == att.id)
        )
        assert result.scalar_one().sent_at is not None
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_chat_blocks.py -v
```

Expected: FAIL (`build_chat_blocks` not defined).

- [ ] **Step 3: Implement `build_chat_blocks`**

Append to `dashboard/backend/app/services/vision_attachments.py`:

```python
import base64

_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


async def build_chat_blocks(
    db: AsyncSession,
    *,
    user_text: str,
    attachment_ids: list[str] | None,
) -> list[dict]:
    """Assemble a multi-block user message: text + attachments.

    Side effect: marks the included attachments as ``sent_at = now()``.
    Caller is responsible for committing.
    """
    blocks: list[dict] = [{"type": "text", "text": user_text}]
    if not attachment_ids:
        return blocks

    now = datetime.now(timezone.utc)
    for aid in attachment_ids:
        att = await db.get(VisionChatAttachment, aid)
        if att is None:
            continue  # silently skip — callers validate IDs upstream
        raw = Path(att.disk_path).read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")

        if att.mime_type == "application/pdf":
            blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                "title": att.filename,
            })
        elif att.mime_type == "text/plain":
            blocks.append({
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": raw.decode("utf-8", errors="replace")},
                "title": att.filename,
            })
        elif att.mime_type in _IMAGE_MIMES:
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": att.mime_type, "data": b64},
            })
        else:
            # Non-native: prepend a header so the model knows what it's looking at
            prefix = f"--- Attached file: {att.filename} ({att.mime_type}) ---\n"
            body = att.extracted_text or "(no extractable text)"
            blocks.append({"type": "text", "text": prefix + body})

        att.sent_at = now

    return blocks
```

- [ ] **Step 4: Wire the blocks into `run_chat_turn`**

Modify `dashboard/backend/app/services/vision_chat.py`. The current `_user_prompt_stream` sends `content: text`. It needs to accept structured content for the multi-block case. Replace `_user_prompt_stream` and update `run_chat_turn`:

```python
async def _user_prompt_stream(content):
    """Yield one user message. `content` may be a str or a list of blocks."""
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }
```

In `run_chat_turn` signature, add an `attachment_blocks` parameter:

```python
async def run_chat_turn(
    db: AsyncSession,
    *,
    session_id: str,
    user_message: str,
    system_prompt: str,
    model: str,
    sdk_session_id: str | None = None,
    attachment_blocks: list[dict] | None = None,
) -> AsyncIterator[dict]:
```

And change the `query(prompt=...)` call:

```python
    if attachment_blocks:
        prompt_content = attachment_blocks
    else:
        prompt_content = user_message

    try:
        async for message in query(prompt=_user_prompt_stream(prompt_content), options=options):
```

The `append_turn` call at the end still records `user_message=user_message` — the SQLite history stores the typed text only; the attachment chips are reconstructed from the `vision_chat_attachments` table on resume (Task 11).

- [ ] **Step 5: Extend the turn schema + router handler**

In `dashboard/backend/app/schemas.py`, add to `VisionChatTurnIn`:

```python
class VisionChatTurnIn(BaseModel):
    """Body for POST /api/projects/{id}/vision/chat (turn)."""
    session_id: str | None = None
    message: str
    attachment_ids: list[str] | None = None
```

In `dashboard/backend/app/routers/vision.py`'s `chat_turn`, before constructing `event_stream`:

```python
    # Validate attachment IDs belong to this session and are unsent
    attachment_blocks: list[dict] | None = None
    if body.attachment_ids:
        from app.models import VisionChatAttachment as _VCA
        from sqlalchemy import select as _select
        result = await db.execute(
            _select(_VCA).where(
                _VCA.id.in_(body.attachment_ids),
                _VCA.session_id == session.id,
                _VCA.sent_at.is_(None),
            )
        )
        rows = list(result.scalars().all())
        if len(rows) != len(body.attachment_ids):
            raise HTTPException(
                status_code=400,
                detail="one or more attachment_ids are invalid, already sent, or from a different session",
            )
        attachment_blocks = await va.build_chat_blocks(
            db, user_text=body.message, attachment_ids=body.attachment_ids,
        )
        await db.commit()  # persist sent_at before the SSE stream starts
```

Then pass `attachment_blocks=attachment_blocks` into `run_chat_turn(...)` inside `event_stream()`.

- [ ] **Step 6: Run the test, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_chat_blocks.py tests/test_vision_chat_service.py -v
```

Expected: PASS for `test_vision_chat_blocks.py`; existing `test_vision_chat_service.py` tests must continue to pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add dashboard/backend/app/services/vision_attachments.py dashboard/backend/app/services/vision_chat.py dashboard/backend/app/routers/vision.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_vision_chat_blocks.py
git commit -m "feat(vision): multi-block chat turns with attachments"
```

---

### Task 10: Commit-time persistence to `docs/vision-refs/`

**Files:**
- Modify: `dashboard/backend/app/services/github_contents.py` — accept bytes
- Modify: `dashboard/backend/app/services/vision_render.py` — `references` argument
- Modify: `dashboard/backend/app/routers/vision.py` — commit loop
- Modify: `dashboard/backend/app/schemas.py` — extend `VisionCommitOut`
- Test: `dashboard/backend/tests/test_vision_commit_references.py`
- Test: `dashboard/backend/tests/test_vision_render.py` — extend
- Test: `dashboard/backend/tests/test_github_contents.py` — extend (if it exists; else create)

- [ ] **Step 1: Extend `render_vision_doc` to accept references**

Modify `dashboard/backend/app/services/vision_render.py`:

```python
def render_vision_doc(
    doc: dict,
    repo: str,
    refined_at: datetime,
    references: list[dict] | None = None,
) -> str:
    """Render a vision_doc dict to the canonical markdown template.

    ``references``: optional list of ``{"filename": str, "size_bytes": int}``;
    when non-empty, appends a ``## References`` section.
    """
    parts = [f"# Vision — {repo}", ""]
    parts.append(f"*Last refined: {refined_at.isoformat()} via Claude Station*")
    parts.append("")
    for key, heading in SECTIONS:
        parts.append(f"## {heading}")
        parts.append("")
        body = (doc.get(key) or "").strip()
        parts.append(body if body else "_(not specified)_")
        parts.append("")
    if references:
        parts.append("## References")
        parts.append("")
        parts.append("Reference files for this vision are in [`vision-refs/`](vision-refs/):")
        parts.append("")
        for ref in references:
            kb = max(1, round(ref["size_bytes"] / 1024))
            parts.append(f"- [`{ref['filename']}`](vision-refs/{ref['filename']}) — {kb} KB")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
```

- [ ] **Step 2: Extend the test for `render_vision_doc`**

Add to `dashboard/backend/tests/test_vision_render.py`:

```python
def test_render_includes_references_when_present():
    from datetime import datetime, timezone
    from app.services.vision_render import render_vision_doc

    md = render_vision_doc(
        {"problem": "x"}, repo="owner/x",
        refined_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
        references=[
            {"filename": "data.xlsx", "size_bytes": 12_345},
            {"filename": "brand.pdf", "size_bytes": 480_000},
        ],
    )
    assert "## References" in md
    assert "[`data.xlsx`](vision-refs/data.xlsx)" in md
    assert "12 KB" in md
    assert "469 KB" in md or "480 KB" in md  # rounding-tolerant


def test_render_omits_references_when_empty_or_none():
    from datetime import datetime, timezone
    from app.services.vision_render import render_vision_doc

    md1 = render_vision_doc({}, repo="x/y", refined_at=datetime(2026, 5, 21, tzinfo=timezone.utc))
    md2 = render_vision_doc({}, repo="x/y", refined_at=datetime(2026, 5, 21, tzinfo=timezone.utc), references=[])
    assert "## References" not in md1
    assert "## References" not in md2
```

Run and confirm:

```bash
cd dashboard/backend && python -m pytest tests/test_vision_render.py -v
```

Expected: PASS for the two new tests + all existing.

- [ ] **Step 3: Extend `github_contents.write_file` to accept bytes**

Modify `dashboard/backend/app/services/github_contents.py`:

```python
async def write_file(
    repo: str,
    path: str,
    branch: str,
    *,
    body: str | None = None,
    body_bytes: bytes | None = None,
    message: str,
    current_sha: str | None,
) -> str:
    """PUT a file to a branch.

    Pass exactly one of ``body`` (utf-8 str) or ``body_bytes`` (raw binary).
    """
    if (body is None) == (body_bytes is None):
        raise ValueError("write_file: pass exactly one of body or body_bytes")

    token = await _get_token()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    raw = body.encode("utf-8") if body is not None else body_bytes
    payload = {
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "branch": branch,
        "committer": COMMIT_AUTHOR,
    }
    if current_sha is not None:
        payload["sha"] = current_sha

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.put(url, headers=headers, json=payload)

    if resp.status_code in (200, 201):
        return resp.json()["content"]["sha"]

    if resp.status_code in (409, 422):
        try:
            current = await read_file(repo=repo, path=path, branch=branch)
        except FileNotFound:
            raise HTTPException(
                status_code=409,
                detail="Concurrent edit and file no longer exists; please retry.",
            )
        raise StaleSha(current_sha=current.sha, current_body=current.body)

    logger.warning("GitHub Contents write failed: %s %s", resp.status_code, resp.text[:200])
    raise HTTPException(status_code=resp.status_code, detail=resp.text)
```

Update the **one existing call site** in `dashboard/backend/app/routers/vision.py`'s `commit_vision`:

```python
        new_sha = await github_contents.write_file(
            repo=project.repo,
            path="docs/vision.md",
            branch=project.branch or "main",
            body=md,
            message=COMMIT_MESSAGE,
            current_sha=project.vision_cached_sha,
        )
```

(the `body=` kwarg is unchanged in spirit; the call site now uses keyword args explicitly because `body` and `body_bytes` are keyword-only).

Grep for other callers to update:

```bash
cd dashboard/backend && grep -rn "github_contents.write_file\|write_file(" app/
```

Update any other callers to use `body=` as a keyword.

- [ ] **Step 4: Extend `VisionCommitOut`**

In `dashboard/backend/app/schemas.py`:

```python
class VisionRefFailure(BaseModel):
    filename: str
    error: str


class VisionCommitOut(BaseModel):
    """Response for POST /api/projects/{id}/vision."""
    sha: str
    html_url: str
    analyst_dispatched: bool = False
    refs_committed: list[str] = []
    refs_failed: list[VisionRefFailure] = []
```

- [ ] **Step 5: Write the commit-flow test**

Create `dashboard/backend/tests/test_vision_commit_references.py`:

```python
"""End-to-end: committing a vision with attachments uploads them to GitHub
and lists them in docs/vision.md (spec 2026-05-21)."""
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import Project, VisionChatAttachment, VisionChatSession
from app.services.vision_attachments import store_attachment


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_commit_writes_vision_md_and_each_reference(
    async_client, async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)

    async with async_session_factory() as db:
        p = Project(repo="owner/x", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        att = await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="data.xlsx")
        # Simulate that the attachment was sent in a turn
        from datetime import datetime, timezone
        att.sent_at = datetime.now(timezone.utc)
        await db.commit()
        pid = p.id

    write_calls = []

    async def fake_write(repo, path, branch, *, body=None, body_bytes=None, message, current_sha):
        write_calls.append({"path": path, "has_body": body is not None, "has_bytes": body_bytes is not None})
        return "fake-sha-" + path.replace("/", "_")

    async def fake_read(repo, path, branch):
        from app.services.github_contents import ContentsResult
        return ContentsResult(sha="fake-vision-sha", body="# Vision — owner/x\n", html_url="https://github.com/owner/x/blob/main/docs/vision.md")

    with patch("app.services.github_contents.write_file", new=AsyncMock(side_effect=fake_write)), \
         patch("app.services.github_contents.read_file", new=AsyncMock(side_effect=fake_read)), \
         patch("app.services.service_control.start_vision_analyst", new=AsyncMock(return_value={"success": True})):
        resp = await async_client.post(
            f"/api/projects/{pid}/vision",
            json={"vision_doc": {"problem": "p"}},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "data.xlsx" in payload["refs_committed"]
    assert payload["refs_failed"] == []

    paths = [c["path"] for c in write_calls]
    assert "docs/vision.md" in paths
    assert "docs/vision-refs/data.xlsx" in paths

    # vision-refs/* must use body_bytes (binary), not body
    ref_calls = [c for c in write_calls if c["path"].startswith("docs/vision-refs/")]
    assert all(c["has_bytes"] and not c["has_body"] for c in ref_calls)


@pytest.mark.asyncio
async def test_commit_skips_unsent_attachments(
    async_client, async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        p = Project(repo="owner/y", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        # No sent_at set — should not be committed
        await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="data.xlsx")
        await db.commit()
        pid = p.id

    write_calls = []

    async def fake_write(*args, **kwargs):
        write_calls.append(kwargs.get("path") or args[1])
        return "sha"

    async def fake_read(repo, path, branch):
        from app.services.github_contents import ContentsResult
        return ContentsResult(sha="sha", body="x", html_url="u")

    with patch("app.services.github_contents.write_file", new=AsyncMock(side_effect=fake_write)), \
         patch("app.services.github_contents.read_file", new=AsyncMock(side_effect=fake_read)), \
         patch("app.services.service_control.start_vision_analyst", new=AsyncMock(return_value={"success": True})):
        resp = await async_client.post(
            f"/api/projects/{pid}/vision",
            json={"vision_doc": {"problem": "p"}},
        )

    assert resp.status_code == 200
    assert resp.json()["refs_committed"] == []
```

- [ ] **Step 6: Implement the commit-flow extension**

In `dashboard/backend/app/routers/vision.py`'s `commit_vision`, after `await vc_service.mark_approved(...)` and before `await db.commit()`:

```python
    # Persist reference files to docs/vision-refs/
    from app.models import VisionChatAttachment
    from app.services import vision_attachments as va
    from pathlib import Path

    refs_committed: list[str] = []
    refs_failed: list[dict] = []
    references_for_render: list[dict] = []

    if active:
        result = await db.execute(
            select(VisionChatAttachment).where(
                VisionChatAttachment.session_id == active.id,
                VisionChatAttachment.sent_at.is_not(None),
            )
        )
        for att in result.scalars().all():
            try:
                raw = Path(att.disk_path).read_bytes()
                await github_contents.write_file(
                    repo=project.repo,
                    path=f"docs/vision-refs/{att.filename}",
                    branch=project.branch or "main",
                    body_bytes=raw,
                    message=f"docs(vision-refs): add {att.filename}",
                    current_sha=None,
                )
                refs_committed.append(att.filename)
                references_for_render.append({
                    "filename": att.filename, "size_bytes": att.size_bytes,
                })
            except Exception as exc:
                logger.warning("vision ref upload failed for %s: %s", att.filename, exc)
                refs_failed.append({"filename": att.filename, "error": str(exc)})

    if refs_committed and not refs_failed:
        # All-or-nothing cleanup: if any failed, retain disk files for retry
        if active:
            va.cleanup_session_dir(active.id)
```

Now thread `references_for_render` into the `render_vision_doc` call earlier in the function:

```python
    md = render_vision_doc(
        body.vision_doc.model_dump(),
        repo=project.repo,
        refined_at=now,
        references=references_for_render,  # may be []
    )
```

**Important**: `render_vision_doc` is called *before* the refs are uploaded today. Move the render+vision.md write to happen *after* the refs loop so the References section reflects what actually committed. Restructure the function:

1. Loop over attachments, write each ref → collect `refs_committed` + `references_for_render` + `refs_failed`.
2. Render `vision.md` with `references=references_for_render`.
3. Write `vision.md` to GitHub.
4. Re-fetch / update cache.
5. Trigger analyst.
6. Cleanup session dir if all good.

Update the `return VisionCommitOut(...)` to include the new fields:

```python
    return VisionCommitOut(
        sha=new_sha, html_url=fresh.html_url, analyst_dispatched=dispatched,
        refs_committed=refs_committed,
        refs_failed=[VisionRefFailure(**rf) for rf in refs_failed],
    )
```

- [ ] **Step 7: Run the commit tests and the existing vision-router tests**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_commit_references.py tests/test_vision_router.py -v
```

Expected: PASS. If `test_vision_router.py` fails because of the call-signature change to `write_file`, update those tests to use `body=` as a kwarg.

- [ ] **Step 8: Commit**

```bash
git add dashboard/backend/app/services/github_contents.py dashboard/backend/app/services/vision_render.py dashboard/backend/app/routers/vision.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_vision_commit_references.py dashboard/backend/tests/test_vision_render.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): persist references to vision-refs/ on commit"
```

---

### Task 11: Surface attachments on session resume + cancel-cleanup

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py` — `get_chat_session` returns attachments per message + currently-pending; `delete_chat_session` cleans disk
- Modify: `dashboard/backend/app/services/vision_chat.py` — record attachments in `messages` JSON on `append_turn`
- Modify: `dashboard/backend/app/schemas.py` — extend `VisionChatSessionOut` with pending attachments
- Test: `dashboard/backend/tests/test_vision_session_resume.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_vision_session_resume.py`:

```python
"""Session resume surfaces attachments; cancel cleans disk (spec 2026-05-21)."""
import io
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.models import Project, VisionChatSession
from app.services.vision_attachments import store_attachment


def _xlsx() -> bytes:
    wb = Workbook(); wb.active.append(["a"]); buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_get_session_includes_pending_attachments(
    async_client, async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        p = Project(repo="owner/r", branch="main")
        db.add(p); await db.commit(); await db.refresh(p)
        sess = VisionChatSession(
            id=str(uuid.uuid4()), project_id=p.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(sess); await db.commit()
        await store_attachment(db, session_id=sess.id, raw=_xlsx(), declared_filename="pending.xlsx")
        await db.commit()
        pid = p.id

    resp = await async_client.get(f"/api/projects/{pid}/vision/chat")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pending_attachments"]) == 1
    assert body["pending_attachments"][0]["filename"] == "pending.xlsx"


@pytest.mark.asyncio
async def test_delete_session_removes_upload_dir(
    async_client, async_session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    async with async_session_factory() as db:
        p = Project(repo="owner/c", branch="main")
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

    resp = await async_client.delete(f"/api/projects/{pid}/vision/chat")
    assert resp.status_code == 204
    assert not upload_dir.exists()
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_session_resume.py -v
```

Expected: FAIL (`pending_attachments` key missing; upload dir not cleaned).

- [ ] **Step 3: Extend `VisionChatSessionOut`**

In `dashboard/backend/app/schemas.py`:

```python
class VisionChatSessionOut(BaseModel):
    """Response for GET /api/projects/{id}/vision/chat."""
    id: str
    project_id: int
    state: str
    phase: str
    coverage: dict
    messages: list[dict]
    assembled: dict | None
    created_at: str
    updated_at: str
    pending_attachments: list[VisionAttachmentOut] = []
```

- [ ] **Step 4: Populate `pending_attachments` in `get_chat_session`**

In `dashboard/backend/app/routers/vision.py`'s `get_chat_session`:

```python
    from app.models import VisionChatAttachment as _VCA
    from app.schemas import VisionAttachmentOut as _VAO
    pending_q = await db.execute(
        select(_VCA).where(_VCA.session_id == session.id, _VCA.sent_at.is_(None))
    )
    pending = [
        _VAO(id=a.id, filename=a.filename, mime_type=a.mime_type, size_bytes=a.size_bytes)
        for a in pending_q.scalars().all()
    ]
    return VisionChatSessionOut(
        ... ,
        pending_attachments=pending,
    )
```

- [ ] **Step 5: Make `delete_chat_session` clean disk**

In the same router:

```python
@router.delete("/{project_id}/vision/chat", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel the active chat session for a project."""
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    sid = session.id
    await mark_cancelled(db, sid)
    await db.commit()
    # Drop disk uploads after the DB row is updated
    from app.services import vision_attachments as va
    va.cleanup_session_dir(sid)
```

- [ ] **Step 6: Record attachments-per-message in `append_turn`**

In `dashboard/backend/app/services/vision_chat.py`, change `append_turn` to accept and persist attachments:

```python
async def append_turn(
    db: AsyncSession,
    session_id: str,
    *,
    user_message: str,
    assistant_message: str,
    coverage: dict | None = None,
    phase: str | None = None,
    sdk_session_id: str | None = None,
    user_attachments: list[dict] | None = None,
) -> VisionChatSession:
    """Append a user→assistant turn and update coverage/phase if provided."""
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)

    msgs = json.loads(session.messages)
    user_msg = {"role": "user", "content": user_message}
    if user_attachments:
        user_msg["attachments"] = user_attachments
    msgs.append(user_msg)
    msgs.append({"role": "assistant", "content": assistant_message})
    session.messages = json.dumps(msgs)
    ...
```

In `run_chat_turn`, when the turn finishes, look up the attachments that were sent on this turn (those with `sent_at` set after the turn started — or accept a `user_attachments` arg from the caller). Simpler: pass them in from the router.

In the router's `chat_turn`, after computing `attachment_blocks`, also compute and pass `user_attachments_dict`:

```python
    user_attachments_dict: list[dict] | None = None
    if body.attachment_ids:
        from app.models import VisionChatAttachment as _VCA
        att_rows = await db.execute(
            select(_VCA).where(_VCA.id.in_(body.attachment_ids))
        )
        user_attachments_dict = [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type, "size_bytes": a.size_bytes}
            for a in att_rows.scalars().all()
        ]
```

Then pass `user_attachments=user_attachments_dict` into `run_chat_turn`, which passes it into `append_turn`.

Add `user_attachments: list[dict] | None = None` to `run_chat_turn`'s signature and forward it to `append_turn(... user_attachments=user_attachments)`.

- [ ] **Step 7: Run all relevant tests**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_session_resume.py tests/test_vision_router.py tests/test_vision_chat_service.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/app/services/vision_chat.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_vision_session_resume.py
git commit -m "feat(vision): session resume surfaces attachments; cancel cleans disk"
```

---

### Task 12: Orphan upload-dir cleanup

**Files:**
- Modify: `dashboard/backend/app/services/vision_cleanup.py`
- Test: `dashboard/backend/tests/test_vision_cleanup.py` — extend

- [ ] **Step 1: Write the failing test**

Add to `dashboard/backend/tests/test_vision_cleanup.py`:

```python
@pytest.mark.asyncio
async def test_sweep_removes_orphan_upload_dirs(async_session_factory, tmp_path, monkeypatch):
    """Upload dirs for sessions that don't exist (or are old & non-active) get removed."""
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr("app.services.vision_attachments._upload_root", lambda: tmp_path)
    import uuid as _uuid
    from app.models import VisionChatSession
    from app.services.vision_cleanup import sweep_stale_sessions

    # 1. Fully orphan dir (no matching session row)
    orphan_id = _uuid.uuid4().hex
    (tmp_path / orphan_id).mkdir()
    (tmp_path / orphan_id / "f.txt").write_text("x")

    # 2. Dir for an approved+old session — should be removed
    async with async_session_factory() as db:
        old = VisionChatSession(
            id=_uuid.uuid4().hex, project_id=1, state="approved",
            phase="freeform", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        db.add(old); await db.commit()
        (tmp_path / old.id).mkdir()
        (tmp_path / old.id / "g.txt").write_text("x")
        old_id = old.id

    # 3. Dir for an active session — must NOT be removed
    async with async_session_factory() as db:
        live = VisionChatSession(
            id=_uuid.uuid4().hex, project_id=1, state="active",
            phase="freeform", coverage="{}", messages="[]",
        )
        db.add(live); await db.commit()
        (tmp_path / live.id).mkdir()
        (tmp_path / live.id / "h.txt").write_text("x")
        live_id = live.id

    async with async_session_factory() as db:
        await sweep_stale_sessions(db)

    assert not (tmp_path / orphan_id).exists()
    assert not (tmp_path / old_id).exists()
    assert (tmp_path / live_id).exists()
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_cleanup.py -v
```

Expected: FAIL.

- [ ] **Step 3: Extend `sweep_stale_sessions`**

In `dashboard/backend/app/services/vision_cleanup.py`, add a helper and call it from `sweep_stale_sessions`:

```python
async def _sweep_orphan_upload_dirs(db: AsyncSession) -> int:
    """Remove upload dirs for sessions that don't exist OR are not active."""
    from app.services.vision_attachments import _upload_root
    import shutil
    from sqlalchemy import select as _select

    root = _upload_root()
    if not root.exists():
        return 0

    result = await db.execute(_select(VisionChatSession.id, VisionChatSession.state))
    rows = {r[0]: r[1] for r in result.all()}

    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        state = rows.get(child.name)
        if state is None or state != "active":
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
```

Call it from `sweep_stale_sessions` after the existing cleanup, before `await db.commit()`:

```python
    orphans = await _sweep_orphan_upload_dirs(db)
    await db.commit()
    return len(stale_active), delete_result.rowcount or 0
```

(Don't bother widening the return tuple — orphans go to logs only.)

In `run_cleanup_loop`, log orphans too:

```python
                cancelled, deleted = await sweep_stale_sessions(db)
                if cancelled or deleted:
                    logger.info("vision_cleanup: cancelled=%d deleted=%d", cancelled, deleted)
```

(The orphan count is logged inside `_sweep_orphan_upload_dirs` if you want; not required.)

- [ ] **Step 4: Run, confirm it passes**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_cleanup.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_cleanup.py dashboard/backend/tests/test_vision_cleanup.py
git commit -m "feat(vision): orphan upload-dir cleanup in stale-session sweep"
```

---

## Phase 3 — Frontend

### Task 13: Types + API client

**Files:**
- Modify: `dashboard/frontend/src/lib/types.ts`
- Modify: `dashboard/frontend/src/lib/api.ts`
- Test: `dashboard/frontend/src/lib/api.attachments.test.ts` (create)

- [ ] **Step 1: Add types**

In `dashboard/frontend/src/lib/types.ts`, find the existing `VisionChatSession` / `VisionMessage` types. Add:

```typescript
export type VisionAttachment = {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
};
```

Extend `VisionMessage` (or whatever the message-shape type is called) with `attachments?: VisionAttachment[]`. Extend `VisionChatSession` with `pending_attachments?: VisionAttachment[]`.

Extend `VisionCommitOut`:

```typescript
export type VisionCommitOut = {
  sha: string;
  html_url: string;
  analyst_dispatched: boolean;
  refs_committed: string[];
  refs_failed: { filename: string; error: string }[];
};
```

- [ ] **Step 2: Write the failing test**

Create `dashboard/frontend/src/lib/api.attachments.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { uploadVisionAttachment, deleteVisionAttachment } from './api';

describe('vision attachment API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    localStorage.setItem('station-api-key', 'test-key');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('uploadVisionAttachment posts multipart with auth header', async () => {
    (global.fetch as any).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 'a1', filename: 'x.xlsx', mime_type: 'm', size_bytes: 5 }),
    });
    const file = new File(['hi'], 'x.xlsx');
    const out = await uploadVisionAttachment(7, file);
    expect(out.id).toBe('a1');
    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/api/projects/7/vision/chat/attachments');
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer test-key');
    expect(init.body).toBeInstanceOf(FormData);
  });

  it('deleteVisionAttachment sends DELETE', async () => {
    (global.fetch as any).mockResolvedValue({ ok: true, status: 204 });
    await deleteVisionAttachment(7, 'a1');
    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/api/projects/7/vision/chat/attachments/a1');
    expect(init.method).toBe('DELETE');
  });
});
```

- [ ] **Step 3: Run, confirm it fails**

```bash
cd dashboard/frontend && npm run test -- --run src/lib/api.attachments.test.ts
```

Expected: FAIL (functions not exported).

- [ ] **Step 4: Implement**

Append to `dashboard/frontend/src/lib/api.ts`:

```typescript
import type { VisionAttachment } from './types';

export async function uploadVisionAttachment(
  projectId: number,
  file: File,
): Promise<VisionAttachment> {
  const apiKey = getStoredApiKey();
  const headers: Record<string, string> = {};
  if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(
    `${BASE}/api/projects/${projectId}/vision/chat/attachments`,
    { method: 'POST', headers, body: form },
  );
  if (!res.ok) {
    const body = await res.text();
    let msg = body;
    try { msg = JSON.parse(body).detail || body; } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json();
}

export async function deleteVisionAttachment(
  projectId: number, attachmentId: string,
): Promise<void> {
  const apiKey = getStoredApiKey();
  const headers: Record<string, string> = {};
  if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
  const res = await fetch(
    `${BASE}/api/projects/${projectId}/vision/chat/attachments/${attachmentId}`,
    { method: 'DELETE', headers },
  );
  if (!res.ok && res.status !== 204) {
    const body = await res.text();
    let msg = body;
    try { msg = JSON.parse(body).detail || body; } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
}
```

- [ ] **Step 5: Run, confirm it passes**

```bash
cd dashboard/frontend && npm run test -- --run src/lib/api.attachments.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/lib/api.ts dashboard/frontend/src/lib/types.ts dashboard/frontend/src/lib/api.attachments.test.ts
git commit -m "feat(vision): frontend attachment types + API client"
```

---

### Task 14: SSE payload extension

**Files:**
- Modify: `dashboard/frontend/src/lib/vision-sse.ts` — accept `attachment_ids` in payload
- Test: `dashboard/frontend/src/lib/vision-sse.test.ts` — extend

- [ ] **Step 1: Inspect the existing payload type**

```bash
cd dashboard/frontend && grep -n "payload\|attachment\|message" src/lib/vision-sse.ts | head -10
```

- [ ] **Step 2: Extend the payload type**

In `dashboard/frontend/src/lib/vision-sse.ts`, find the `payload` type/interface argument and add:

```typescript
payload: { session_id: string | null; message: string; attachment_ids?: string[] };
```

The body serialisation already JSON-stringifies the payload, so no logic changes — only the type widens.

- [ ] **Step 3: Add a test that the field passes through**

Append to `dashboard/frontend/src/lib/vision-sse.test.ts`:

```typescript
it('passes attachment_ids in the request body', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    body: new ReadableStream({ start(c) { c.close(); } }),
  });
  vi.stubGlobal('fetch', fetchMock);

  const iter = streamVisionChat({
    url: '/test',
    headers: {},
    payload: { session_id: null, message: 'hi', attachment_ids: ['a1', 'a2'] },
    signal: new AbortController().signal,
  });
  for await (const _ of iter) {}

  const [, init] = fetchMock.mock.calls[0];
  const body = JSON.parse(init.body);
  expect(body.attachment_ids).toEqual(['a1', 'a2']);

  vi.unstubAllGlobals();
});
```

- [ ] **Step 4: Run, confirm it passes (no implementation change needed beyond the type)**

```bash
cd dashboard/frontend && npm run test -- --run src/lib/vision-sse.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/vision-sse.ts dashboard/frontend/src/lib/vision-sse.test.ts
git commit -m "feat(vision): SSE chat payload accepts attachment_ids"
```

---

### Task 15: VisionChat.svelte — paperclip, dropzone, chips

**Files:**
- Modify: `dashboard/frontend/src/components/vision/VisionChat.svelte`
- Test: `dashboard/frontend/src/components/vision/VisionChat.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `dashboard/frontend/src/components/vision/VisionChat.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import VisionChat from './VisionChat.svelte';

vi.mock('../../lib/api', () => ({
  uploadVisionAttachment: vi.fn().mockResolvedValue({
    id: 'a1', filename: 'data.xlsx', mime_type: 'x', size_bytes: 12345,
  }),
  deleteVisionAttachment: vi.fn().mockResolvedValue(undefined),
  getVisionChatSession: vi.fn().mockRejectedValue(new Error('404')),
  cancelVisionChat: vi.fn(),
  commitVision: vi.fn(),
  getStoredApiKey: () => 'k',
  visionChatTurnUrl: (n: number) => `/api/projects/${n}/vision/chat`,
}));

vi.mock('../../lib/vision-sse', () => ({
  streamVisionChat: vi.fn(() => (async function*() { yield { type: 'done' }; })()),
}));

vi.mock('../../lib/toast.svelte', () => ({
  toastError: vi.fn(), toastSuccess: vi.fn(), addToast: vi.fn(),
}));

describe('VisionChat attachments', () => {
  it('uploads selected file and shows chip', async () => {
    const { getByTestId, findByText } = render(VisionChat, { props: { projectId: 1 } });
    const input = getByTestId('vision-chat-attach-input') as HTMLInputElement;

    const file = new File(['hi'], 'data.xlsx');
    await fireEvent.change(input, { target: { files: [file] } });

    await findByText('data.xlsx');
  });

  it('removes a pending chip on × click', async () => {
    const { getByTestId, findByText, queryByText } = render(VisionChat, { props: { projectId: 1 } });
    const input = getByTestId('vision-chat-attach-input') as HTMLInputElement;
    await fireEvent.change(input, { target: { files: [new File(['hi'], 'data.xlsx')] } });
    await findByText('data.xlsx');
    const remove = getByTestId('vision-chat-attachment-remove-a1');
    await fireEvent.click(remove);
    await waitFor(() => expect(queryByText('data.xlsx')).toBeNull());
  });
});
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd dashboard/frontend && npm run test -- --run src/components/vision/VisionChat.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement the UI**

Edit `dashboard/frontend/src/components/vision/VisionChat.svelte`. Add to the imports:

```typescript
import { streamVisionChat } from '../../lib/vision-sse';
import {
  visionChatTurnUrl, getVisionChatSession, cancelVisionChat,
  commitVision, getStoredApiKey,
  uploadVisionAttachment, deleteVisionAttachment,
} from '../../lib/api';
import type { VisionDoc, VisionSseEvent, VisionAttachment } from '../../lib/types';
```

Add to state:

```typescript
let pendingAttachments = $state<VisionAttachment[]>([]);
let uploadingCount = $state(0);
let attachInput: HTMLInputElement;

const ALLOWED_EXT = '.pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.csv,.xlsx,.docx';
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_SESSION_BYTES = 40 * 1024 * 1024;
```

Add helpers:

```typescript
function pendingTotalBytes(): number {
  return pendingAttachments.reduce((sum, a) => sum + a.size_bytes, 0);
}

async function handleFiles(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    if (file.size > MAX_FILE_BYTES) {
      toastError(`${file.name} is ${Math.round(file.size / 1024 / 1024)} MB — max 10 MB per file`);
      continue;
    }
    if (pendingTotalBytes() + file.size > MAX_SESSION_BYTES) {
      toastError(`Adding ${file.name} would exceed the 40 MB session limit`);
      continue;
    }
    uploadingCount++;
    try {
      const att = await uploadVisionAttachment(projectId, file);
      pendingAttachments = [...pendingAttachments, att];
    } catch (e: any) {
      toastError(e.message ?? 'upload failed');
    } finally {
      uploadingCount--;
    }
  }
}

async function removeAttachment(id: string) {
  try {
    await deleteVisionAttachment(projectId, id);
  } catch (e: any) {
    toastError(e.message ?? 'delete failed');
    return;
  }
  pendingAttachments = pendingAttachments.filter(a => a.id !== id);
}

function onDrop(e: DragEvent) {
  e.preventDefault();
  if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
}
function onDragOver(e: DragEvent) { e.preventDefault(); }
function onPick(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) handleFiles(input.files);
  input.value = '';
}
```

Extend `resume()` to rehydrate `pendingAttachments` from `s.pending_attachments ?? []`:

```typescript
async function resume() {
  try {
    const s = await getVisionChatSession(projectId);
    sessionId = s.id;
    messages = s.messages.map(m => ({ role: m.role, content: m.content, attachments: m.attachments }));
    covered = Object.entries(s.coverage).filter(([, v]) => v).map(([k]) => k);
    phase = s.phase;
    if (s.assembled) assembledDoc = s.assembled;
    pendingAttachments = s.pending_attachments ?? [];
  } catch {
    // 404 = no active session
  }
}
```

Update `Msg` type and `send()`:

```typescript
type Msg = { role: 'user' | 'assistant'; content: string; attachments?: VisionAttachment[] };
```

In `send()`:

```typescript
  const attachmentsForTurn = pendingAttachments;
  pendingAttachments = [];
  messages = [
    ...messages,
    { role: 'user', content: text, attachments: attachmentsForTurn.length ? attachmentsForTurn : undefined },
    { role: 'assistant', content: '' },
  ];
  // ...
  for await (const ev of streamVisionChat({
    url: visionChatTurnUrl(projectId),
    headers,
    payload: {
      session_id: sessionId,
      message: text,
      attachment_ids: attachmentsForTurn.map(a => a.id),
    },
    signal: abortCtrl.signal,
  })) {
    handleEvent(ev);
  }
```

In the markup, wrap the transcript card with the dropzone handlers and add the chip strip + paperclip:

```svelte
<div
  class="card p-4 max-h-96 overflow-y-auto space-y-3"
  data-testid="vision-chat-transcript"
  ondragover={onDragOver}
  ondrop={onDrop}
>
  {#if messages.length === 0}
    <p class="text-xs text-tertiary">Hi — describe your project in your own words…</p>
  {/if}
  {#each messages as m, i (i)}
    <div class="text-sm">
      <div class="text-[10px] font-semibold text-tertiary mb-1">{m.role === 'user' ? 'You' : 'Claude'}</div>
      <div class="whitespace-pre-wrap text-secondary">{m.content || (streaming && i === messages.length - 1 ? '…' : '')}</div>
      {#if m.attachments?.length}
        <div class="flex flex-wrap gap-1 mt-1">
          {#each m.attachments as a (a.id)}
            <span class="inline-flex items-center gap-1 text-[10px] bg-tertiary/10 px-2 py-0.5 rounded">📎 {a.filename}</span>
          {/each}
        </div>
      {/if}
    </div>
  {/each}
</div>

<!-- Pending attachment chips above the input -->
{#if pendingAttachments.length || uploadingCount}
  <div class="flex flex-wrap gap-1" data-testid="vision-chat-pending-strip">
    {#each pendingAttachments as a (a.id)}
      <span class="inline-flex items-center gap-1 text-[10px] bg-tertiary/15 px-2 py-1 rounded">
        📎 {a.filename} · {Math.max(1, Math.round(a.size_bytes / 1024))} KB
        <button
          type="button"
          class="text-tertiary hover:text-primary"
          aria-label="Remove {a.filename}"
          data-testid={`vision-chat-attachment-remove-${a.id}`}
          onclick={() => removeAttachment(a.id)}
        >×</button>
      </span>
    {/each}
    {#if uploadingCount > 0}
      <span class="text-[10px] text-tertiary">Uploading {uploadingCount}…</span>
    {/if}
  </div>
{/if}

<!-- Input + attach button -->
<div class="flex gap-2">
  <input
    type="file"
    bind:this={attachInput}
    accept={ALLOWED_EXT}
    multiple
    onchange={onPick}
    class="hidden"
    data-testid="vision-chat-attach-input"
  />
  <button
    type="button"
    onclick={() => attachInput.click()}
    class="btn btn-ghost btn-sm text-xs"
    data-testid="vision-chat-attach-btn"
    aria-label="Attach reference file"
  >📎</button>
  <input
    type="text"
    bind:value={input}
    placeholder="Type a message…"
    class="input flex-1 text-sm"
    disabled={streaming || uploadingCount > 0}
    onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') send(); }}
    data-testid="vision-chat-input"
  />
  <button
    type="button"
    onclick={send}
    disabled={streaming || uploadingCount > 0 || !input.trim()}
    class="btn btn-primary btn-sm text-xs"
  >Send</button>
</div>
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
cd dashboard/frontend && npm run test -- --run src/components/vision/VisionChat.test.ts
```

Expected: PASS.

- [ ] **Step 5: Run the dev server + smoke-test manually**

```bash
cd dashboard/backend && uvicorn app.main:app --reload --port 8420 &
cd dashboard/frontend && npm run dev
```

Open `http://localhost:5173`, navigate to a project's Vision tab, click "Start vision chat", click the paperclip, pick an xlsx + a PDF + a PNG. Confirm chips appear. Type a message and Send. Confirm the assistant response references the attached content. Open DevTools Network tab and verify the SSE request body contains `attachment_ids`.

Note in commit message: tested manually with [list of files].

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/components/vision/VisionChat.svelte dashboard/frontend/src/components/vision/VisionChat.test.ts
git commit -m "feat(vision): attachments UI — paperclip, dropzone, chips"
```

---

## Phase 4 — Downstream + docs

### Task 16: Update agent prompts + docs

**Files:**
- Modify: `agent/prompts/employee.md`
- Modify: `agent/vision_analyst.py` — enumerate `docs/vision-refs/`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/deployment.md` (libmagic note)

- [ ] **Step 1: Add references awareness to employee prompt**

In `agent/prompts/employee.md`, find a sensible location (near where the prompt discusses reading the vision or the project context) and append:

```
## Reference files

The project may have reference files committed under `docs/vision-refs/`
(uploaded during a vision chat). When relevant to your task, read them
— csv/xlsx files often contain tabular data the issue depends on; PDFs
and images describe brand, design, or external systems. Use the
appropriate tool to inspect them (e.g., `openpyxl` for xlsx, image
preview tools for PNG/JPEG).
```

- [ ] **Step 2: Make the vision analyst list references**

In `agent/vision_analyst.py`, find where the prompt is assembled (the function that reads `docs/vision.md`). Add a step that lists `docs/vision-refs/` and injects:

```python
def _list_vision_refs(repo_root: Path) -> str:
    """Return a markdown bullet list of files under docs/vision-refs/, or empty string."""
    refs_dir = repo_root / "docs" / "vision-refs"
    if not refs_dir.exists():
        return ""
    files = sorted(p for p in refs_dir.iterdir() if p.is_file())
    if not files:
        return ""
    lines = ["## Reference files", ""]
    lines += [f"- `docs/vision-refs/{p.name}` ({p.stat().st_size // 1024 or 1} KB)" for p in files]
    return "\n".join(lines) + "\n"
```

And include the result in the prompt body the analyst sends. Locate the existing prompt assembly (likely a string format / f-string) and inject `{refs_section}` right after the vision body.

- [ ] **Step 3: Update docs**

In `docs/configuration.md`, find the "Environment variables" / "Storage paths" section. Add:

```markdown
- `VISION_UPLOAD_DIR` — directory where vision chat attachments live before commit.
  Default: `/var/lib/claude-agent-station/vision-chat-uploads`.
  The directory must be writable by the backend process; entries are cleaned up on
  session approve/cancel and by the periodic vision-cleanup sweep.
```

Also add to the Database tables list:

```markdown
- `vision_chat_attachments` — reference files attached to a vision chat. One row per
  upload, scoped to a `vision_chat_sessions.id` (cascade delete).
```

Add to the Backend dependencies section:

```markdown
- `openpyxl`, `python-docx` — text extraction for non-native attachment types.
- `python-magic` — MIME sniffing (requires system `libmagic`; on Rocky/RHEL install with `dnf install file-libs file-devel`).
```

In `docs/architecture.md`, find the Vision section and add a short subsection:

```markdown
### Vision reference files

During a vision chat, users may attach PDFs / images / xlsx / csv / docx / txt / md
files (≤ 10 MB each, ≤ 40 MB total per session). Uploads land on disk under
`VISION_UPLOAD_DIR/<session_id>/` and are mirrored in the `vision_chat_attachments`
table. PDFs / images are sent to Claude as native blocks; other types are extracted
server-side (openpyxl, python-docx) and sent as text. On commit, the files are
PUT to `docs/vision-refs/` in the target repo and listed in a `## References`
section of `docs/vision.md`. Teammates pick them up automatically via `git clone`.

See spec: `docs/superpowers/specs/2026-05-21-vision-reference-files-design.md`.
```

In `docs/deployment.md`, in the system-dependencies section:

```markdown
- `libmagic` — required by python-magic for vision attachment MIME sniffing.
  On Rocky/RHEL: `dnf install file-libs file-devel`.
```

- [ ] **Step 4: Run a smoke test of the analyst path**

```bash
cd dashboard/backend && python -m pytest tests/test_vision_analyst.py -v
```

Expected: PASS (existing tests should still pass; we added a no-op-if-missing helper).

- [ ] **Step 5: Commit**

```bash
git add agent/prompts/employee.md agent/vision_analyst.py docs/configuration.md docs/architecture.md docs/deployment.md
git commit -m "feat(vision): teammate + analyst awareness of vision-refs; docs"
```

---

### Task 17: Full backend + frontend test sweep

**Files:** (verification only)

- [ ] **Step 1: Run the full backend test suite**

```bash
cd dashboard/backend && python -m pytest -q
```

Expected: all tests PASS. Any failures must be in code we touched — diagnose and fix.

- [ ] **Step 2: Run the full frontend test suite**

```bash
cd dashboard/frontend && npm run test -- --run
```

Expected: all tests PASS.

- [ ] **Step 3: Type check frontend**

```bash
cd dashboard/frontend && npm run check
```

Expected: 0 errors.

- [ ] **Step 4: Lint backend (if a linter is configured)**

```bash
cd dashboard/backend && ruff check . 2>/dev/null || echo "no ruff configured"
```

If ruff or a linter is configured, address any new findings.

- [ ] **Step 5: Manual end-to-end test**

Start the backend + frontend, create a vision for a test project, attach:
- A PDF (e.g., any short PDF lying around)
- A PNG screenshot
- An xlsx (export anything from LibreOffice — three columns, 5 rows)
- A CSV

Confirm:
- All four chips appear above the input.
- Send works; Claude references the contents in its reply.
- The "Approve & commit" button writes `docs/vision.md` AND all four files to `docs/vision-refs/` in the test repo (check on GitHub).
- The committed `docs/vision.md` has a `## References` section listing all four files.
- Clone the test repo locally and confirm the binary xlsx opens correctly.

- [ ] **Step 6: Commit the test plan results**

If you needed any fixes in step 1–4, commit them. Otherwise no commit needed.

```bash
git status
```

If clean, move on.

---

### Task 18: Open the PR

**Files:** (PR only)

- [ ] **Step 1: Push branch and open PR against `dev`**

```bash
git push -u origin feature/vision-reference-files-spec
gh pr create --base dev --title "feat: vision reference file uploads (spec 2026-05-21)" --body "$(cat <<'EOF'
## Summary
- Lets users attach reference files (PDF, images, xlsx, csv, docx, txt, md) during the vision chat
- PDFs and images sent to Claude as native blocks; xlsx/csv/docx extracted server-side
- Files committed to the project repo under `docs/vision-refs/` on Approve, listed in a `## References` section of `docs/vision.md`
- Teammates pick them up automatically via `git clone`; vision analyst enumerates them in its prompt

Spec: `docs/superpowers/specs/2026-05-21-vision-reference-files-design.md`

## Test plan
- [ ] Backend tests: `cd dashboard/backend && python -m pytest -q`
- [ ] Frontend tests: `cd dashboard/frontend && npm run test -- --run`
- [ ] Frontend type check: `cd dashboard/frontend && npm run check`
- [ ] Manual: attach PDF + PNG + xlsx + CSV to a vision chat, Send, then Approve. Verify the four files land in the project repo's `docs/vision-refs/` and the rendered `docs/vision.md` has a References section.
- [ ] Manual: refresh mid-chat, confirm pending attachment chips rehydrate.
- [ ] Manual: cancel a vision chat with pending attachments, confirm `VISION_UPLOAD_DIR/<session-id>/` is removed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Self-Review (completed by plan author)

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| File-type matrix (native + extracted) | Tasks 5, 6, 9 |
| Size limits (10 MB / 40 MB / 200 KB) | Tasks 6, 7 |
| Paperclip + chips + dropzone UI | Task 15 |
| Validation toasts (oversize / unsupported) | Tasks 8, 15 |
| `vision_chat_attachments` table | Tasks 1, 2 |
| Storage layout under `VISION_UPLOAD_DIR` | Tasks 3, 7 |
| POST `.../attachments` endpoint | Task 8 |
| DELETE `.../attachments/{id}` (with `sent_at` guard) | Task 8 |
| Extended `.../chat` accepting `attachment_ids` | Task 9 |
| Extended `.../vision` commit writing refs | Task 10 |
| Extended DELETE `.../chat` cleaning disk | Task 11 |
| `github_contents.write_file` accepting bytes | Task 10 |
| `render_vision_doc` References section | Task 10 |
| Session-resume rehydration of attachment chips | Tasks 11, 15 |
| Orphan-dir cleanup in periodic sweep | Task 12 |
| Frontend types + API client | Task 13 |
| SSE payload extension | Task 14 |
| Teammate prompt note + analyst awareness | Task 16 |
| Docs updates (`configuration.md`, `architecture.md`, `deployment.md`) | Task 16 |
| Migration (Alembic 0005) | Task 2 |
| Dependencies (openpyxl, python-docx, python-magic) | Task 3 |

All spec requirements have a task. No gaps.

**Type / signature consistency check:**

- `VisionChatAttachment` columns match between model (Task 1), migration (Task 2), and store/usage (Tasks 6–11).
- `sent_at` is the consistent name used across model, store guard, delete guard, commit filter, and resume filter.
- `pending_attachments` is the consistent field name across schema (Task 11), GET handler (Task 11), and frontend (Tasks 13, 15).
- `attachment_ids` is the consistent payload key across SSE payload (Task 14), router validation (Task 9), and frontend send (Task 15).
- `references: list[dict]` (each `{filename, size_bytes}`) matches between `render_vision_doc` extension (Task 10), commit-flow construction (Task 10), and rendered-section assertion (Task 10 test).

**Placeholder scan:** no TBDs, no "add appropriate handling", no "similar to Task N", no "fill in details". Every code step ships actual code.
