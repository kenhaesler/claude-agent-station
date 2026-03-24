"""Sprint cycle status and findings API."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import AgentEvent, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sprint", tags=["sprint"])


def _workspace_for_repo(repo: str) -> Path:
    """Return the workspace path for a given owner/repo string."""
    name = repo.split("/")[-1] if "/" in repo else repo
    return Path(settings.workspaces_dir) / name


def _read_json(path: Path) -> dict | list | None:
    """Read and parse a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# GET /api/sprint/status/{repo:path}
# ---------------------------------------------------------------------------


@router.get("/status/{repo:path}")
async def sprint_status(repo: str, db: AsyncSession = Depends(get_db)):
    """Return the current sprint status for a project.

    Reads the latest sprint brief from the workspace and supplements with
    recent webhook events for live role progress.
    """
    workspace = _workspace_for_repo(repo)
    sprint_dir = workspace / ".claude-sprint"
    brief_path = sprint_dir / "brief.json"

    brief = _read_json(brief_path)
    if not brief:
        return {"status": "no_sprint", "repo": repo}

    sprint_id = brief.get("sprint_id", "")

    # Collect role statuses from webhook events
    result = await db.execute(
        select(AgentEvent)
        .where(
            AgentEvent.event_type.in_(["role_start", "role_complete"]),
        )
        .order_by(AgentEvent.created_at.desc())
        .limit(50)
    )
    events = result.scalars().all()

    # Filter to events matching this sprint_id
    role_statuses: dict[str, dict] = {}
    for e in events:
        data = e.event_data if isinstance(e.event_data, dict) else {}
        if data.get("sprint_id") != sprint_id:
            continue
        role = data.get("role", "")
        if not role:
            continue
        if role not in role_statuses:
            role_statuses[role] = {
                "role": role,
                "status": "unknown",
                "proposals_count": 0,
            }
        if e.event_type == "role_complete":
            role_statuses[role]["status"] = "complete"
            role_statuses[role]["proposals_count"] = data.get("proposals_count", 0)
        elif e.event_type == "role_start" and role_statuses[role]["status"] == "unknown":
            role_statuses[role]["status"] = "running"

    # Check for sprint_complete event
    complete_result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.event_type == "sprint_complete")
        .order_by(AgentEvent.created_at.desc())
        .limit(5)
    )
    complete_events = complete_result.scalars().all()
    sprint_complete = False
    for e in complete_events:
        data = e.event_data if isinstance(e.event_data, dict) else {}
        if data.get("sprint_id") == sprint_id:
            sprint_complete = True
            break

    return {
        "status": "complete" if sprint_complete else "running",
        "repo": repo,
        "sprint_id": sprint_id,
        "brief": brief,
        "roles": list(role_statuses.values()),
    }


# ---------------------------------------------------------------------------
# GET /api/sprint/findings/{sprint_id}
# ---------------------------------------------------------------------------


@router.get("/findings/{sprint_id}")
async def sprint_findings(sprint_id: str, db: AsyncSession = Depends(get_db)):
    """Return all role findings for a sprint.

    Scans workspace directories to find the matching sprint and reads all
    role findings.json files.
    """
    workspaces = Path(settings.workspaces_dir)
    if not workspaces.is_dir():
        raise HTTPException(status_code=404, detail="Workspaces directory not found")

    # Search all workspaces for a matching sprint brief
    for ws in workspaces.iterdir():
        if not ws.is_dir():
            continue
        brief_path = ws / ".claude-sprint" / "brief.json"
        brief = _read_json(brief_path)
        if not brief or brief.get("sprint_id") != sprint_id:
            continue

        # Found the right workspace — collect all role findings
        sprint_dir = ws / ".claude-sprint"
        all_findings: dict[str, dict | list | None] = {}
        for role_dir in sorted(sprint_dir.iterdir()):
            if not role_dir.is_dir():
                continue
            findings_path = role_dir / "findings.json"
            if findings_path.is_file():
                all_findings[role_dir.name] = _read_json(findings_path)

        return {
            "sprint_id": sprint_id,
            "brief": brief,
            "findings": all_findings,
        }

    raise HTTPException(status_code=404, detail=f"Sprint {sprint_id} not found")


# ---------------------------------------------------------------------------
# GET /api/sprint/findings/{sprint_id}/{role}
# ---------------------------------------------------------------------------


@router.get("/findings/{sprint_id}/{role}")
async def sprint_role_findings(sprint_id: str, role: str, db: AsyncSession = Depends(get_db)):
    """Return a single role's findings for a sprint."""
    workspaces = Path(settings.workspaces_dir)
    if not workspaces.is_dir():
        raise HTTPException(status_code=404, detail="Workspaces directory not found")

    for ws in workspaces.iterdir():
        if not ws.is_dir():
            continue
        brief_path = ws / ".claude-sprint" / "brief.json"
        brief = _read_json(brief_path)
        if not brief or brief.get("sprint_id") != sprint_id:
            continue

        findings_path = ws / ".claude-sprint" / role / "findings.json"
        if not findings_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"No findings for role '{role}' in sprint {sprint_id}",
            )

        findings = _read_json(findings_path)
        return {
            "sprint_id": sprint_id,
            "role": role,
            "findings": findings,
        }

    raise HTTPException(status_code=404, detail=f"Sprint {sprint_id} not found")


# ---------------------------------------------------------------------------
# GET /api/sprint/history/{repo:path}
# ---------------------------------------------------------------------------


@router.get("/history/{repo:path}")
async def sprint_history(repo: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Return sprint history summaries from webhook events."""
    result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.event_type == "sprint_complete")
        .order_by(AgentEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()

    # Filter to events for this repo
    history = []
    for e in events:
        data = e.event_data if isinstance(e.event_data, dict) else {}
        if data.get("project") != repo:
            continue
        history.append({
            "sprint_id": data.get("sprint_id"),
            "project": data.get("project"),
            "roles_completed": data.get("roles_completed", 0),
            "total_proposals": data.get("total_proposals", 0),
            "auto_implement": data.get("auto_implement", "false"),
            "timestamp": e.created_at.isoformat() if e.created_at else None,
        })

    return {"repo": repo, "history": history}
