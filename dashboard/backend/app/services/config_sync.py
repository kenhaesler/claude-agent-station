from __future__ import annotations

"""Bidirectional sync between station-config.json and DB projects table.

JSON is the source of truth for the agent. The DB mirrors it for the API.
- On startup: JSON -> DB (upsert)
- On API mutation: DB -> JSON (atomic rewrite)
"""

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Project

logger = logging.getLogger(__name__)


def _read_config_json() -> dict[str, Any]:
    """Read the station config JSON file."""
    path = Path(settings.config_path)
    if not path.exists():
        return {"projects": []}
    with open(path) as f:
        return json.load(f)


def _write_config_json(config: dict[str, Any]) -> None:
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
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


async def sync_config_to_db(db: AsyncSession) -> int:
    """Read JSON config and upsert projects into DB. Returns count of synced projects."""
    config = await asyncio.to_thread(_read_config_json)
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
            existing.promotion_target = proj_data.get(
                "promotion_target", existing.promotion_target
            )
            existing.custom_instructions = proj_data.get(
                "custom_instructions", existing.custom_instructions
            )
            existing.setup_script = proj_data.get(
                "setup_script", existing.setup_script
            )
            existing.security_review_enabled = proj_data.get(
                "security_review_enabled", existing.security_review_enabled or False
            )
        else:
            project = Project(
                repo=repo,
                priority=proj_data.get("priority", "medium"),
                mode=proj_data.get("mode", "full"),
                enabled=proj_data.get("enabled", True),
                branch=proj_data.get("branch", "main"),
                promotion_target=proj_data.get("promotion_target"),
                custom_instructions=proj_data.get("custom_instructions"),
                setup_script=proj_data.get("setup_script"),
                security_review_enabled=proj_data.get("security_review_enabled", False),
            )
            db.add(project)
        count += 1

    await db.commit()
    logger.info("Synced %d projects from config JSON to DB", count)
    return count


async def sync_db_to_config(db: AsyncSession) -> None:
    """Rewrite JSON config from current DB state."""
    config = await asyncio.to_thread(_read_config_json)

    result = await db.execute(select(Project))
    projects = result.scalars().all()

    config["projects"] = [
        {
            "repo": p.repo,
            "priority": p.priority,
            "mode": p.mode,
            **({"enabled": p.enabled} if not p.enabled else {}),
            **({"branch": p.branch} if p.branch != "main" else {}),
            **({"promotion_target": p.promotion_target} if p.promotion_target else {}),
            **({"custom_instructions": p.custom_instructions} if p.custom_instructions else {}),
            **({"setup_script": p.setup_script} if p.setup_script else {}),
            **({"security_review_enabled": True} if p.security_review_enabled else {}),
        }
        for p in projects
        if True  # include disabled projects so they're not lost
    ]

    await asyncio.to_thread(_write_config_json, config)
