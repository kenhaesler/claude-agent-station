"""Vision attachment helpers: sanitise filenames, sniff MIME, extract text.

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.
"""
from __future__ import annotations

import os
import re

import magic

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
