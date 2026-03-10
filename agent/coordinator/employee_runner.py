"""Subprocess wrapper for spawning Claude employees."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import Task

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _build_employee_prompt(task: Task, config: CoordinatorConfig, employee_index: int) -> str:
    """Build the employee prompt for a coordinated task."""
    report_suffix = f"-{employee_index}" if employee_index > 0 else ""

    # Check for assignment file
    assignment_file = Path(task.workspace) / f".claude-assignment-{employee_index}.json"
    assignment_section = ""
    if assignment_file.exists():
        try:
            assignment = json.loads(assignment_file.read_text())
            assign_issue = assignment.get("issue_number", "")
            assign_title = assignment.get("issue_title", "")
            assign_instructions = assignment.get("instructions", "")
            assignment_section = f"""
## DIRECTED MODE: Pre-Assigned Issue

The manager has assigned you a specific issue. Do NOT pick your own issue.

- **Issue**: #{assign_issue}
- **Title**: {assign_title}
- **Manager Instructions**: {assign_instructions}

Skip Step 1 (Find Work). Go directly to Step 1b, then proceed with Step 2.
"""
        except (json.JSONDecodeError, OSError):
            pass

    # Build task context for coordinated mode
    deps_section = ""
    if task.depends_on:
        deps_section = f"""
## Dependency Context

This task depends on completed tasks: {', '.join(task.depends_on)}
Their work has been committed. You can build on their changes.
"""

    files_section = ""
    if task.expected_files:
        files_section = f"""
## Expected Files

You are expected to primarily work on these files:
{chr(10).join(f'- {f}' for f in task.expected_files)}

Focus on these files. Other employees are working on different parts of the codebase.
"""

    prompt = f"""Work on the repository: {task.project_repo}

Environment variables available:
- GITHUB_REPO={task.project_repo}
- GH_TOKEN is set

Your workspace is: {task.workspace}

## Coordinated Task Assignment

You are employee #{employee_index} working on a specific sub-task as part of a coordinated effort.
Other employees are working on related sub-tasks in parallel or sequence.

**Task**: {task.title}
**Description**: {task.description}
{assignment_section}
{deps_section}
{files_section}

## Guidance Channel

The manager may send you real-time guidance during your work.
Every 5-10 tool calls, check if `.claude-guidance-{employee_index}.json` exists in your workspace root.
If it exists:
1. Read it
2. Follow the instructions based on type (warning/redirect/stop/info)
3. Delete the file after reading to acknowledge receipt

Write your report to {task.workspace}/.claude-employee-report{report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."""

    return prompt


def _get_stream_file(config: CoordinatorConfig, project_repo: str, employee_index: int) -> str:
    """Get the stream file path matching run-manager.sh conventions."""
    repo_name = project_repo.split("/")[-1] if "/" in project_repo else project_repo
    return os.path.join(
        config.log_dir,
        f"run-{config.run_id}-employee-{repo_name}-e{employee_index}.stream.jsonl",
    )


async def run_employee(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
) -> tuple[int, str]:
    """Spawn a Claude employee subprocess for a task.

    Returns (exit_code, stream_file_path).
    Matches the CLI invocation pattern from run-manager.sh run_employee().
    """
    prompt = _build_employee_prompt(task, config, employee_index)
    system_prompt_file = str(PROMPTS_DIR / "employee.md")
    stream_file = _get_stream_file(config, task.project_repo, employee_index)
    stderr_file = stream_file.replace(".stream.jsonl", ".stderr.log")

    # Calculate per-employee turn budget
    max_turns = config.max_employee_turns
    running_count = max(1, config.max_concurrent)
    if running_count > 1:
        max_turns = max(50, max_turns // running_count)

    # Determine fallback model
    model = config.employee_model
    fallback_model = "claude-sonnet-4-6" if model != "claude-sonnet-4-6" else "claude-haiku-4-5-20251001"

    env = os.environ.copy()
    env["GITHUB_REPO"] = task.project_repo

    logger.info(
        "Spawning employee %d for task '%s' (model=%s, turns=%d, workspace=%s)",
        employee_index, task.title, model, max_turns, task.workspace,
    )

    # Open stream file for writing
    stream_path = Path(stream_file)
    stream_path.parent.mkdir(parents=True, exist_ok=True)

    # Match run-manager.sh invocation exactly:
    #   claude -p --verbose --output-format stream-json --no-session-persistence
    #     --dangerously-skip-permissions --model X --fallback-model Y
    #     --max-turns N --system-prompt-file FILE -- "$prompt"
    stderr_fh = open(stderr_file, "w")
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--verbose",
            "--output-format", "stream-json",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--model", model,
            "--fallback-model", fallback_model,
            "--max-turns", str(max_turns),
            "--system-prompt-file", system_prompt_file,
            "--", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_fh,
            cwd=task.workspace,
            env=env,
        )

        # Stream stdout to file in real-time
        with open(stream_file, "w") as sf:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                sf.write(line.decode())
                sf.flush()

        await proc.wait()
    finally:
        stderr_fh.close()

    exit_code = proc.returncode or 0

    # Log stderr if employee failed
    if exit_code != 0:
        try:
            stderr_content = Path(stderr_file).read_text().strip()
            if stderr_content:
                logger.warning("Employee %d stderr: %s", employee_index, stderr_content[:500])
        except OSError:
            pass

    logger.info(
        "Employee %d for task '%s' exited with code %d",
        employee_index, task.title, exit_code,
    )

    return exit_code, stream_file
