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
