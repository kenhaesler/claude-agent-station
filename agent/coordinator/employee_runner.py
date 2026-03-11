"""Subprocess wrapper for spawning Claude employees."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import Task

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Patterns that indicate Claude CLI rate limiting or plan exhaustion
RATE_LIMIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"plan.{0,20}(limit|exhaust|exceed)", re.IGNORECASE),
    re.compile(r"usage.{0,20}(limit|exhaust|exceed)", re.IGNORECASE),
    re.compile(r"credit.{0,20}(exhaust|exceed|depleted)", re.IGNORECASE),
    re.compile(r"budget.{0,20}(exhaust|exceed|depleted)", re.IGNORECASE),
    re.compile(r"capacity", re.IGNORECASE),
    re.compile(r"throttl", re.IGNORECASE),
    re.compile(r"verwendet.*100%", re.IGNORECASE),  # German: "100% verwendet"
    re.compile(r"100%\s*verwendet", re.IGNORECASE),
]

# Exit codes from Claude CLI that may indicate rate limiting
# Non-zero exit codes that aren't simple user errors
RATE_LIMIT_EXIT_CODES: set[int] = {2, 75, 69}
# 2 = generic error (often API failure), 75 = tempfail, 69 = unavailable


@dataclass
class EmployeeResult:
    """Result from running a Claude employee subprocess.

    Contains exit code, stream file path, and rate limit detection info.
    """

    exit_code: int
    stream_file: str
    rate_limited: bool = False
    rate_limit_reason: str = ""
    stderr_snippet: str = ""
    stdout_snippet: str = ""


def detect_rate_limit_in_text(text: str) -> tuple[bool, str]:
    """Check if text contains rate limit / plan exhaustion indicators.

    Args:
        text: Text content to scan (stderr, stdout, or stream content).

    Returns:
        (is_rate_limited, matched_pattern_description)
    """
    if not text:
        return False, ""

    for pattern in RATE_LIMIT_PATTERNS:
        match = pattern.search(text)
        if match:
            # Extract context around the match for the reason
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip().replace("\n", " ")
            return True, f"Pattern '{pattern.pattern}' matched: ...{context}..."

    return False, ""


def _check_stream_for_rate_limits(stream_file: str) -> tuple[bool, str]:
    """Scan stream JSONL file for rate limit error events.

    Claude CLI stream-json output includes error events that may
    indicate rate limiting.

    Args:
        stream_file: Path to the .stream.jsonl file.

    Returns:
        (is_rate_limited, reason)
    """
    try:
        content = Path(stream_file).read_text()
    except OSError:
        return False, ""

    # Check last 50 lines (rate limits usually appear at the end)
    lines = content.strip().split("\n")
    tail_lines = lines[-50:] if len(lines) > 50 else lines

    for line in tail_lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Also check raw line text for rate limit patterns
            found, reason = detect_rate_limit_in_text(line)
            if found:
                return True, reason
            continue

        # Check error events
        event_type = event.get("type", "")
        if event_type in ("error", "system"):
            error_msg = str(event.get("error", event.get("message", "")))
            found, reason = detect_rate_limit_in_text(error_msg)
            if found:
                return True, f"Stream error event: {reason}"

        # Check for API error subtype
        if event.get("subtype") == "error" or event.get("is_error"):
            content_str = str(event.get("content", ""))
            found, reason = detect_rate_limit_in_text(content_str)
            if found:
                return True, f"Stream error content: {reason}"

    return False, ""


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
) -> EmployeeResult:
    """Spawn a Claude employee subprocess for a task.

    Returns an EmployeeResult with exit code, stream file path,
    and rate limit detection information.

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

    # Read stderr for analysis
    stderr_content = ""
    try:
        stderr_content = Path(stderr_file).read_text().strip()
    except OSError:
        pass

    # Log stderr if employee failed
    if exit_code != 0 and stderr_content:
        logger.warning("Employee %d stderr: %s", employee_index, stderr_content[:500])

    logger.info(
        "Employee %d for task '%s' exited with code %d",
        employee_index, task.title, exit_code,
    )

    # --- Rate limit detection ---
    rate_limited = False
    rate_limit_reason = ""

    # Check 1: Scan stderr for rate limit indicators
    if stderr_content:
        found, reason = detect_rate_limit_in_text(stderr_content)
        if found:
            rate_limited = True
            rate_limit_reason = f"stderr: {reason}"
            logger.warning(
                "Employee %d RATE LIMITED (stderr): %s",
                employee_index, reason,
            )

    # Check 2: Scan stream file for rate limit error events
    if not rate_limited:
        found, reason = _check_stream_for_rate_limits(stream_file)
        if found:
            rate_limited = True
            rate_limit_reason = f"stream: {reason}"
            logger.warning(
                "Employee %d RATE LIMITED (stream): %s",
                employee_index, reason,
            )

    # Check 3: Non-zero exit code that commonly indicates rate limiting
    # Only flag if exit code is in the known set AND the employee produced
    # very little output (suggesting it failed early due to API rejection)
    if not rate_limited and exit_code in RATE_LIMIT_EXIT_CODES:
        try:
            stream_size = Path(stream_file).stat().st_size
        except OSError:
            stream_size = 0

        # If the process failed quickly with minimal output, likely rate limited
        if stream_size < 1024:
            rate_limited = True
            rate_limit_reason = (
                f"Exit code {exit_code} with minimal output "
                f"({stream_size} bytes) suggests plan exhaustion"
            )
            logger.warning(
                "Employee %d RATE LIMITED (exit code): %s",
                employee_index, rate_limit_reason,
            )

    return EmployeeResult(
        exit_code=exit_code,
        stream_file=stream_file,
        rate_limited=rate_limited,
        rate_limit_reason=rate_limit_reason,
        stderr_snippet=stderr_content[:500] if stderr_content else "",
        stdout_snippet="",  # stdout goes to stream file
    )
