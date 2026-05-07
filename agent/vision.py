"""Load and parse docs/vision.md from a project workspace.

Returns a dict shaped like the VisionDoc Pydantic model in the dashboard.
Missing file → None. Missing sections → empty strings (orchestrator hooks
treat empty sections as "no signal").
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def load_vision(workspace_dir: str) -> dict[str, Any] | None:
    """Return the parsed vision doc, or None if docs/vision.md is missing."""
    path = os.path.join(workspace_dir, "docs", "vision.md")
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        logger.warning("could not read vision file %s: %s", path, e)
        return None

    sections: dict[str, str] = {key: "" for key, _ in SECTIONS}

    # Find each H2 heading and capture text until the next H2 or EOF
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        # Match heading to canonical section key
        for key, label in SECTIONS:
            if heading.lower() == label.lower():
                sections[key] = body
                break

    return sections
