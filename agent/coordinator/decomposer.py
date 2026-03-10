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

from agent.coordinator.dag import TaskDAG

logger = logging.getLogger(__name__)

DECOMPOSITION_PROMPT = """You are a task decomposition agent. Given a GitHub issue, split it into sub-tasks that can be worked on by separate employees (Claude Code agents).

Rules:
1. Each sub-task should be independently implementable by one employee
2. Define clear dependencies between tasks (which tasks must complete before others can start)
3. Aim for 2-5 sub-tasks. Don't over-decompose simple issues.
4. Each task should touch different files to minimize conflicts
5. Include a final integration/test task that depends on all implementation tasks
6. If the issue is simple enough for one employee, return a single task

Return ONLY valid JSON in this exact format:
{
  "tasks": [
    {
      "title": "Short task title",
      "description": "What this employee should do",
      "depends_on": [],
      "expected_files": ["path/to/file1.py", "path/to/file2.py"]
    },
    {
      "title": "Second task",
      "description": "Build on task 0's work",
      "depends_on": [0],
      "expected_files": ["path/to/other.py"]
    }
  ]
}

The depends_on field uses task indices (0-based) from this array."""


def decompose_issue(
    issue_body: str,
    repo: str,
    workspace: str,
    config: CoordinatorConfig,
    issue_number: int | None = None,
    employee_count: int = 2,
) -> TaskDAG:
    """Decompose a GitHub issue into a task DAG.

    Uses Claude Haiku for fast, cheap decomposition.
    Falls back to a single-task DAG on any failure.
    """
    # For single employee, skip decomposition
    if employee_count <= 1:
        logger.info("Single employee — skipping decomposition")
        return TaskDAG.single_task(
            config.run_id, repo,
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
            return _fallback_dag(config, repo, issue_body, issue_number)

        return _parse_decomposition(result.stdout, config, repo, issue_number)

    except subprocess.TimeoutExpired:
        logger.warning("Decomposition agent timed out, using single task")
        return _fallback_dag(config, repo, issue_body, issue_number)
    except Exception as e:
        logger.warning("Decomposition failed: %s, using single task", e)
        return _fallback_dag(config, repo, issue_body, issue_number)


def _parse_decomposition(output: str, config: CoordinatorConfig, repo: str, issue_number: int | None) -> TaskDAG:
    """Parse the decomposition agent's JSON output into a TaskDAG."""
    # Extract JSON from potential markdown wrapping
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    text = match.group(1) if match else output.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse decomposition JSON, using single task")
        return _fallback_dag(config, repo, "", issue_number)

    tasks_data = data.get("tasks", [])
    if not tasks_data or len(tasks_data) < 1:
        logger.warning("Decomposition returned empty tasks, using single task")
        return _fallback_dag(config, repo, "", issue_number)

    dag = TaskDAG(config.run_id, repo)

    # Map index-based depends_on to task IDs
    task_ids: list[str] = []
    for i, td in enumerate(tasks_data):
        deps = []
        for dep_idx in td.get("depends_on", []):
            if isinstance(dep_idx, int) and 0 <= dep_idx < len(task_ids):
                deps.append(task_ids[dep_idx])

        task = dag.add_task(
            title=td.get("title", f"Task {i}"),
            description=td.get("description", ""),
            depends_on=deps,
            issue_number=issue_number,
            expected_files=td.get("expected_files", []),
        )
        task_ids.append(task.id)

    logger.info("Decomposed issue into %d tasks", len(task_ids))
    return dag


def _fallback_dag(config: CoordinatorConfig, repo: str, description: str, issue_number: int | None) -> TaskDAG:
    """Create a single-task DAG as fallback."""
    return TaskDAG.single_task(
        config.run_id, repo,
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
