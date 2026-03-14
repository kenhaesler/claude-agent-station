"""Task decomposition using Claude to split issues into sub-tasks."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.coordinator.dag import TaskDAG

logger = logging.getLogger(__name__)

DECOMPOSITION_PROMPT = """<identity>
You are a task decomposition agent. Given a GitHub issue, split it into sub-tasks for separate employee agents (Claude Code).
</identity>

<rules>
1. Each sub-task must be independently implementable by one employee.
2. Each sub-task should touch at most 5 files.
3. Define clear dependencies (which tasks must complete before others can start).
4. Aim for 2-5 sub-tasks. Don't over-decompose simple issues.
5. Each task should touch different files to minimize merge conflicts.
6. Include a final integration/test task that depends on all implementation tasks.
7. If the issue is simple enough for one employee, return a single task.
</rules>

<output-format>
Return ONLY valid JSON in this exact format:

{
  "tasks": [
    {
      "title": "Short task title",
      "description": "What this employee should do",
      "depends_on": [],
      "expected_files": ["path/to/file1.py", "path/to/file2.py"]
    }
  ]
}

The depends_on field uses 0-based task indices from this array.
</output-format>

<examples>
Input: "Add user profile page with avatar upload and activity feed"

Good decomposition:
{
  "tasks": [
    {
      "title": "Add profile page layout and routing",
      "description": "Create the profile page component with routing. Add basic layout with placeholder sections for avatar and activity feed.",
      "depends_on": [],
      "expected_files": ["src/routes/profile.svelte", "src/routes/+layout.svelte"]
    },
    {
      "title": "Implement avatar upload backend and UI",
      "description": "Add avatar upload endpoint and file storage. Add the upload widget to the profile page.",
      "depends_on": [0],
      "expected_files": ["src/api/avatar.py", "src/components/AvatarUpload.svelte", "tests/test_avatar.py"]
    },
    {
      "title": "Implement activity feed",
      "description": "Add activity feed query and component. Wire into the profile page layout.",
      "depends_on": [0],
      "expected_files": ["src/api/activity.py", "src/components/ActivityFeed.svelte", "tests/test_activity.py"]
    },
    {
      "title": "Integration testing",
      "description": "Run full test suite. Verify profile page renders with avatar and activity feed. Test edge cases (no avatar, empty feed).",
      "depends_on": [1, 2],
      "expected_files": ["tests/test_profile_integration.py"]
    }
  ]
}
</examples>"""


async def decompose_issue(
    issue_body: str,
    repo: str,
    workspace: str,
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str | None = None,
    issue_number: int | None = None,
    employee_count: int = 2,
) -> TaskDAG:
    """Decompose a GitHub issue into a task DAG.

    Uses Claude Haiku for fast, cheap decomposition.
    Falls back to a single-task DAG on any failure.
    """
    effective_run_id = run_id or config.run_id

    # For analyze mode, create a single read-only analysis task
    if config.project_mode == "analyze":
        logger.info("Analyze mode — creating read-only analysis task")
        title = (
            f"Analyze codebase for issue #{issue_number}"
            if issue_number
            else "Analyze codebase"
        )
        return await TaskDAG.single_task(
            effective_run_id, repo, session_factory,
            title=title,
            description=issue_body[:2000],
            issue_number=issue_number,
        )

    # For single employee, skip decomposition
    if employee_count <= 1:
        logger.info("Single employee — skipping decomposition")
        return await TaskDAG.single_task(
            effective_run_id, repo, session_factory,
            title=f"Implement issue #{issue_number}" if issue_number else "Implement feature",
            description=issue_body[:2000],
            issue_number=issue_number,
        )

    # Get repo file listing for context
    file_list = _get_file_listing(workspace)

    prompt = f"""{DECOMPOSITION_PROMPT}

## Issue #{issue_number or 'N/A'}

{issue_body[:3000]}

## Repository Files (top-level structure)

{file_list}

## Target Employee Count: {employee_count}

Decompose this issue into sub-tasks for {employee_count} employees."""

    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", config.decomposition_model,
                "--max-turns", "1",
                "--no-session-persistence",
                "--dangerously-skip-permissions",
                "--output-format", "text",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workspace,
        )

        if result.returncode != 0:
            logger.warning("Decomposition agent failed (exit %d), using single task", result.returncode)
            return await _fallback_dag(config, session_factory, repo, issue_body, issue_number, effective_run_id)

        return await _parse_decomposition(result.stdout, config, session_factory, repo, issue_number, effective_run_id)

    except subprocess.TimeoutExpired:
        logger.warning("Decomposition agent timed out, using single task")
        return await _fallback_dag(config, session_factory, repo, issue_body, issue_number, effective_run_id)
    except Exception as e:
        logger.warning("Decomposition failed: %s, using single task", e)
        return await _fallback_dag(config, session_factory, repo, issue_body, issue_number, effective_run_id)


async def _parse_decomposition(
    output: str,
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    repo: str,
    issue_number: int | None,
    run_id: str | None = None,
) -> TaskDAG:
    """Parse the decomposition agent's JSON output into a TaskDAG."""
    effective_run_id = run_id or config.run_id

    # Extract JSON from potential markdown wrapping
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    text = match.group(1) if match else output.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse decomposition JSON, using single task")
        return await _fallback_dag(config, session_factory, repo, "", issue_number, effective_run_id)

    tasks_data = data.get("tasks", [])
    if not tasks_data or len(tasks_data) < 1:
        logger.warning("Decomposition returned empty tasks, using single task")
        return await _fallback_dag(config, session_factory, repo, "", issue_number, effective_run_id)

    dag = TaskDAG(effective_run_id, repo, session_factory)

    # Map index-based depends_on to task IDs
    task_ids: list[str] = []
    for i, td in enumerate(tasks_data):
        deps = []
        for dep_idx in td.get("depends_on", []):
            if isinstance(dep_idx, int) and 0 <= dep_idx < len(task_ids):
                deps.append(task_ids[dep_idx])

        task = await dag.add_task(
            title=td.get("title", f"Task {i}"),
            description=td.get("description", ""),
            depends_on=deps,
            issue_number=issue_number,
            expected_files=td.get("expected_files", []),
        )
        task_ids.append(task.id)

    logger.info("Decomposed issue into %d tasks", len(task_ids))
    return dag


async def _fallback_dag(
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    repo: str,
    description: str,
    issue_number: int | None,
    run_id: str | None = None,
) -> TaskDAG:
    """Create a single-task DAG as fallback."""
    effective_run_id = run_id or config.run_id
    return await TaskDAG.single_task(
        effective_run_id, repo, session_factory,
        title=f"Implement issue #{issue_number}" if issue_number else "Implement feature",
        description=description[:2000],
        issue_number=issue_number,
    )


def _get_file_listing(workspace: str, max_depth: int = 3) -> str:
    """Get a condensed file listing of the workspace."""
    try:
        result = subprocess.run(
            ["find", ".", "-maxdepth", str(max_depth), "-type", "f",
             "-not", "-path", "./.git/*", "-not", "-path", "./node_modules/*",
             "-not", "-path", "./.venv/*"],
            capture_output=True, text=True, cwd=workspace, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        # Limit to 100 most relevant files
        return "\n".join(lines[:100])
    except Exception:
        return "(file listing unavailable)"
