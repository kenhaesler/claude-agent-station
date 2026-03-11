"""System prompt management endpoints.

Allows reading default prompts and storing custom overrides.
Custom overrides are persisted in:
  1. The config DB (key-value store, key = "prompt_override_{role}")
  2. A file at agent/prompts/custom/{role}.md (for the shell scripts)
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import ConfigEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

# Prompt roles and their default file locations (relative to this file)
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent  # dashboard/backend
_AGENT_DIR = _BACKEND_DIR.parent / "agent"
_PROMPTS_DIR = _AGENT_DIR / "prompts"
_CUSTOM_DIR = _PROMPTS_DIR / "custom"

PROMPT_ROLES = {
    "manager": {
        "label": "Manager",
        "description": "Reviews employee work and issues verdicts (APPROVE/PR/REJECT)",
        "file": "manager.md",
    },
    "employee": {
        "label": "Employee",
        "description": "Executes work on issues — implements features and fixes bugs",
        "file": "employee.md",
    },
    "analyst": {
        "label": "Analyst",
        "description": "Analyzes codebase for bugs, debt, and improvement opportunities",
        "file": "analyst.md",
    },
    "planner": {
        "label": "Planner",
        "description": "Creates detailed implementation plans for open issues",
        "file": "planner.md",
    },
    "assigner": {
        "label": "Assigner",
        "description": "Distributes issues among parallel employees to prevent duplicates",
        "file": "assigner.md",
    },
}

DB_KEY_PREFIX = "prompt_override_"


def _read_default(role: str) -> str:
    """Read the default prompt file content for a role."""
    info = PROMPT_ROLES.get(role)
    if not info:
        return ""
    path = _PROMPTS_DIR / info["file"]
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _write_custom_file(role: str, content: str) -> None:
    """Write a custom prompt override to the filesystem."""
    _CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    path = _CUSTOM_DIR / f"{role}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote custom prompt file: %s", path)


def _delete_custom_file(role: str) -> None:
    """Remove the custom prompt override file."""
    path = _CUSTOM_DIR / f"{role}.md"
    if path.exists():
        path.unlink()
        logger.info("Deleted custom prompt file: %s", path)


@router.get("")
async def list_prompts(db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    """List all prompt roles with default content and custom overrides."""
    # Fetch all overrides from DB in one query
    keys = [f"{DB_KEY_PREFIX}{role}" for role in PROMPT_ROLES]
    result = await db.execute(
        select(ConfigEntry).where(ConfigEntry.key.in_(keys))
    )
    overrides = {e.key: e.value for e in result.scalars().all()}

    prompts = []
    for role, info in PROMPT_ROLES.items():
        db_key = f"{DB_KEY_PREFIX}{role}"
        custom = overrides.get(db_key)
        prompts.append({
            "role": role,
            "label": info["label"],
            "description": info["description"],
            "default_content": _read_default(role),
            "custom_content": custom,  # None if no override, raw string if set
            "has_override": custom is not None,
        })

    return prompts


@router.get("/{role}")
async def get_prompt(role: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Get a single prompt role with its default and custom content."""
    if role not in PROMPT_ROLES:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown prompt role: {role}")

    info = PROMPT_ROLES[role]
    db_key = f"{DB_KEY_PREFIX}{role}"

    result = await db.execute(
        select(ConfigEntry).where(ConfigEntry.key == db_key)
    )
    entry = result.scalar_one_or_none()

    return {
        "role": role,
        "label": info["label"],
        "description": info["description"],
        "default_content": _read_default(role),
        "custom_content": entry.value if entry else None,
        "has_override": entry is not None,
    }


@router.put("/{role}")
async def update_prompt(
    role: str,
    body: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Save a custom prompt override for a role.

    Body: { "content": "..." }
    """
    if role not in PROMPT_ROLES:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown prompt role: {role}")

    content = body.get("content", "")
    if not isinstance(content, str) or not content.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Content must be a non-empty string")

    db_key = f"{DB_KEY_PREFIX}{role}"

    # Upsert in DB (store as raw text, not JSON-encoded)
    result = await db.execute(
        select(ConfigEntry).where(ConfigEntry.key == db_key)
    )
    entry = result.scalar_one_or_none()

    if entry:
        entry.value = content
    else:
        entry = ConfigEntry(key=db_key, value=content)
        db.add(entry)

    await db.commit()

    # Also write to filesystem for shell script consumption
    _write_custom_file(role, content)

    logger.info("Updated custom prompt for role: %s", role)
    info = PROMPT_ROLES[role]
    return {
        "role": role,
        "label": info["label"],
        "description": info["description"],
        "default_content": _read_default(role),
        "custom_content": content,
        "has_override": True,
    }


@router.delete("/{role}")
async def reset_prompt(role: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Reset a prompt to its default (remove custom override)."""
    if role not in PROMPT_ROLES:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown prompt role: {role}")

    db_key = f"{DB_KEY_PREFIX}{role}"

    result = await db.execute(
        select(ConfigEntry).where(ConfigEntry.key == db_key)
    )
    entry = result.scalar_one_or_none()

    if entry:
        await db.delete(entry)
        await db.commit()

    # Remove custom file
    _delete_custom_file(role)

    logger.info("Reset prompt to default for role: %s", role)
    info = PROMPT_ROLES[role]
    return {
        "role": role,
        "label": info["label"],
        "description": info["description"],
        "default_content": _read_default(role),
        "custom_content": None,
        "has_override": False,
    }
