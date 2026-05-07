"""Extract `vision-meta` and `vision-doc` fenced JSON blocks from model output.

The chat backend strips these blocks from text before forwarding `assistant_text`
SSE events to the client, so the user sees clean prose; the parsed metadata
drives `coverage_update`/`phase_change` events and the final assembly.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_META_RE = re.compile(r"```vision-meta\s*\n(.*?)\n```", re.DOTALL)
_DOC_RE = re.compile(r"```vision-doc\s*\n(.*?)\n```", re.DOTALL)
_FENCE_RE = re.compile(r"```vision-(meta|doc)\s*\n.*?\n```\n?", re.DOTALL)

_REQUIRED_DOC_KEYS = {
    "problem", "users", "end_state", "non_goals",
    "principles", "horizons", "anti_patterns",
}


def extract_vision_meta(text: str) -> dict[str, Any] | None:
    """Return the parsed vision-meta JSON, or None if missing/malformed."""
    match = _META_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.debug("vision-meta JSON parse failed: %s", e)
        return None


def extract_vision_doc(text: str) -> dict[str, Any] | None:
    """Return the parsed vision-doc JSON if present and structurally complete."""
    match = _DOC_RE.search(text)
    if not match:
        return None
    try:
        doc = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.debug("vision-doc JSON parse failed: %s", e)
        return None
    if not isinstance(doc, dict):
        return None
    if not _REQUIRED_DOC_KEYS.issubset(doc.keys()):
        logger.debug("vision-doc missing required keys: have %s", doc.keys())
        return None
    return doc


def strip_fenced_blocks(text: str) -> str:
    """Remove vision-meta and vision-doc fences from text for client display."""
    return _FENCE_RE.sub("", text).rstrip() + "\n"
