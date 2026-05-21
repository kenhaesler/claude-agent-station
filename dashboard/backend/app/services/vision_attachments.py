"""Vision attachment helpers: sanitise filenames, sniff MIME, extract text.

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.
"""
from __future__ import annotations

import io
import os
import re

import magic
from openpyxl import load_workbook

# Anthropic's forbidden-character set plus path separators
_FORBIDDEN = re.compile(r'[<>:"|?*\\/]')


def sanitize_filename(raw: str) -> str:
    """Strip path components and forbidden characters; cap length at 255.

    Raises ValueError if nothing usable remains.
    """
    base = os.path.basename(raw.replace("\\", "/"))
    cleaned = _FORBIDDEN.sub("", base)
    cleaned = cleaned.lstrip(". ").rstrip(" ")
    if not cleaned:
        raise ValueError("filename empty after sanitisation")
    if len(cleaned) > 255:
        root, ext = os.path.splitext(cleaned)
        keep = 255 - len(ext)
        cleaned = root[:keep] + ext
    return cleaned


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


EXTRACTION_MAX_BYTES = 200_000  # 200 KB cap on per-file extracted text


def _truncate(text: str) -> str:
    if len(text.encode("utf-8")) <= EXTRACTION_MAX_BYTES:
        return text
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
        out.append("")
    return "\n".join(out)


def _csv_to_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _docx_to_text(data: bytes) -> str:
    from docx import Document  # local import: python-docx is a heavier dependency
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


_EXTRACTORS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _xlsx_to_markdown,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _docx_to_text,
    "text/csv": _csv_to_text,
    "text/markdown": _csv_to_text,  # pass-through; markdown is already text
    # text/plain is also passthrough but Claude takes it natively as a document block.
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
