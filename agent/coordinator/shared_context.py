"""Shared team context for inter-employee awareness.

Writes a .claude-team-context.json file that employees check to know
what other team members are working on, which files to avoid, and
the overall team status.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.stream_monitor import EmployeeActivity

logger = logging.getLogger(__name__)


def write_team_context(
    workspace: str,
    activities: dict[str, EmployeeActivity],
) -> None:
    """Write shared context file so employees know what others are doing.

    Atomic write to {workspace}/.claude-team-context.json
    """
    team: list[dict] = []
    conflict_zones: list[str] = []

    # Build per-employee status
    all_files: dict[str, list[int]] = {}  # file -> list of employee indices

    for activity in activities.values():
        team.append({
            "employee": activity.employee_index,
            "task": activity.task_id,
            "files_being_edited": sorted(list(activity.files_touched))[:15],
            "tool_calls": activity.tool_calls,
            "status": "stuck" if getattr(activity, 'is_stuck', False) else "working",
        })

        for f in activity.files_touched:
            if f not in all_files:
                all_files[f] = []
            all_files[f].append(activity.employee_index)

    # Identify conflict zones (files touched by multiple employees)
    for f, employees in all_files.items():
        if len(employees) > 1:
            conflict_zones.append(f)

    context = {
        "team": team,
        "conflict_zones": sorted(conflict_zones),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Atomic write
    path = os.path.join(workspace, ".claude-team-context.json")
    fd, tmp = tempfile.mkstemp(dir=workspace, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(context, f, indent=2)
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
