"""File-based guidance channel for mid-flight employee corrections."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def send_guidance(
    workspace: str,
    employee_index: int,
    guidance_type: str,
    content: str,
) -> None:
    """Write a guidance file for an employee to pick up.

    Types: warning, redirect, stop, info
    """
    guidance = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": guidance_type,
        "content": content,
    }

    path = Path(workspace) / f".claude-guidance-{employee_index}.json"

    # Atomic write: write to temp file then rename
    fd, tmp = tempfile.mkstemp(dir=workspace, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(guidance, f)
        os.rename(tmp, str(path))
        logger.info("Guidance sent to employee %d: [%s] %s", employee_index, guidance_type, content[:80])
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clear_guidance(workspace: str, employee_index: int) -> None:
    """Remove guidance file (called after employee acknowledges)."""
    path = Path(workspace) / f".claude-guidance-{employee_index}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
