"""Bidirectional sync between station-config.json and DB projects table.

JSON is the source of truth for the agent. The DB mirrors it for the API.
- On startup: JSON -> DB (upsert)
- On API mutation: DB -> JSON (atomic rewrite)
"""

import json
import os
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Project

logger = logging.getLogger(__name__)


def _read_config_json() -> Dict[str, Any]:
    """Read the station config JSON file."""
    path = Path(settings.config_path)
    if not path.exists():
        return {"projects": []}
    with open(path, "r") as f:
        return json.load(f)


def _write_config_json(config: Dict[str, Any]) -> None:
    """Atomically write config JSON (write to temp + os.replace)."""
    path = Path(settings.config_path)
    dir_path = path.parent

    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
        logger.info("Config JSON written to %s", path)
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def sync_config_to_db(db: AsyncSession) -> int:
    """Read JSON config and upsert projects into DB. Returns count of synced projects."""
    config = _read_config_json()
    projects = config.get("projects", [])
    count = 0

    for proj_data in projects:
        repo = proj_data.get("repo")
        if not repo:
            continue

        result = await db.execute(select(Project).where(Project.repo == repo))
        existing = result.scalar_one_or_none()

        if existing:
            existing.priority = proj_data.get("priority", existing.priority)
            existing.mode = proj_data.get("mode", existing.mode)
            existing.enabled = proj_data.get("enabled", True)
            existing.branch = proj_data.get("branch", existing.branch or "main")
        else:
            project = Project(
                repo=repo,
                priority=proj_data.get("priority", "medium"),
                mode=proj_data.get("mode", "full"),
                enabled=proj_data.get("enabled", True),
                branch=proj_data.get("branch", "main"),
            )
            db.add(project)
        count += 1

    await db.commit()
    logger.info("Synced %d projects from config JSON to DB", count)
    return count


async def sync_db_to_config(db: AsyncSession) -> None:
    """Rewrite JSON config from current DB state."""
    config = _read_config_json()

    result = await db.execute(select(Project))
    projects = result.scalars().all()

    config["projects"] = [
        {
            "repo": p.repo,
            "priority": p.priority,
            "mode": p.mode,
            **({"enabled": p.enabled} if not p.enabled else {}),
            **({"branch": p.branch} if p.branch != "main" else {}),
        }
        for p in projects
        if p.enabled or True  # include disabled projects so they're not lost
    ]

    _write_config_json(config)
