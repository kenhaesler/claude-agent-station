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


def _load_assignment(workspace: str, employee_index: int) -> dict:
    """Load .claude-assignment-{index}.json, return dict or empty dict."""
    assignment_file = Path(workspace) / f".claude-assignment-{employee_index}.json"
    if assignment_file.exists():
        try:
            return json.loads(assignment_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _build_employee_prompt(task: Task, config: CoordinatorConfig, employee_index: int) -> str:
    """Build the employee prompt for a coordinated task."""
    report_suffix = f"-{employee_index}" if employee_index > 0 else ""

    # Check for assignment file
    assignment = _load_assignment(task.workspace, employee_index)
    assignment_section = ""
    if assignment:
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


def _build_plan_prompt(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
    revision_feedback: str | None = None,
) -> str:
    """Build the employee prompt for plan-only mode."""
    report_suffix = f"-{employee_index}" if employee_index > 0 else ""
    plan_file = Path(task.workspace) / f".claude-employee-plan-{employee_index}.json"

    revision_section = ""
    if revision_feedback:
        revision_section = f"""
## PLAN_REVISION: Manager Feedback on Previous Plan

The manager reviewed your previous plan and requested changes:

{revision_feedback}

Revise your plan based on this feedback and write the updated plan.
"""

    # Check for assignment file
    assignment = _load_assignment(task.workspace, employee_index)
    assignment_section = ""
    if assignment:
        assign_issue = assignment.get("issue_number", "")
        assign_title = assignment.get("issue_title", "")
        assignment_section = f"""
## DIRECTED MODE: Pre-Assigned Issue

- **Issue**: #{assign_issue}
- **Title**: {assign_title}
"""

    prompt = f"""Work on the repository: {task.project_repo}

Environment variables available:
- GITHUB_REPO={task.project_repo}
- GH_TOKEN is set

Your workspace is: {task.workspace}

## PLAN_ONLY_MODE

You are in **plan-only mode**. Create an implementation plan but do NOT write any code.

1. Read the issue fully (including all comments)
2. Read all relevant source code
3. Create a detailed implementation plan
4. Write the plan JSON to: {plan_file}
5. Write your report to: {task.workspace}/.claude-employee-report{report_suffix}.json with mode "plan_only"
6. STOP. Do not implement anything.
{assignment_section}
{revision_section}

Remember: Plan only. Do NOT create branches, modify source files, or commit anything."""

    return prompt


def _summarize_plan_for_implementation(plan: dict) -> str:
    """Extract only implementation-relevant fields from an approved plan.

    Drops review-only fields (risks, approach, summary) that add token noise
    during implementation without aiding execution.
    """
    essential_keys = ("steps", "files_to_modify", "files_to_create", "testing_strategy")
    summary = {k: plan[k] for k in essential_keys if k in plan}
    # Preserve issue reference if present
    for key in ("issue_number", "issue_title"):
        if key in plan:
            summary[key] = plan[key]
    return json.dumps(summary, indent=2)


def _build_implement_with_plan_prompt(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
    approved_plan: dict,
) -> str:
    """Build the employee prompt for implementation with an approved plan."""
    report_suffix = f"-{employee_index}" if employee_index > 0 else ""
    plan_json = _summarize_plan_for_implementation(approved_plan)

    # Include assignment section if present
    assignment = _load_assignment(task.workspace, employee_index)
    assignment_section = ""
    if assignment:
        assign_issue = assignment.get("issue_number", "")
        assign_title = assignment.get("issue_title", "")
        assignment_section = f"""
## DIRECTED MODE: Pre-Assigned Issue

- **Issue**: #{assign_issue}
- **Title**: {assign_title}

Skip Step 1 (Find Work). Go directly to Step 1b, then Step 2.
"""

    prompt = f"""Work on the repository: {task.project_repo}

Environment variables available:
- GITHUB_REPO={task.project_repo}
- GH_TOKEN is set

Your workspace is: {task.workspace}

## APPROVED_PLAN: Implementation Plan (Manager-Approved)

You have a pre-approved implementation plan. Follow it as your guide, but use your judgment
if you discover the plan needs adjustment during implementation.

```json
{plan_json}
```

Implement each step, write tests, run the full pipeline, and verify everything works.
{assignment_section}

Write your report to {task.workspace}/.claude-employee-report{report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."""

    return prompt


async def _run_claude_subprocess(
    *,
    prompt: str,
    system_prompt_file: str,
    model: str,
    fallback_model: str,
    max_turns: int,
    stream_file: str,
    cwd: str,
    env: dict[str, str],
    label: str = "employee",
) -> EmployeeResult:
    """Spawn a Claude CLI subprocess with stream capture and rate limit detection.

    This is the single shared implementation for all Claude subprocess spawning:
    employee work, plan phase, and manager plan review.
    """
    stderr_file = stream_file.replace(".stream.jsonl", ".stderr.log")

    Path(stream_file).parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Spawning %s subprocess (model=%s, turns=%d)",
        label, model, max_turns,
    )

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
            cwd=cwd,
            env=env,
        )

        # Stream stdout to file in real-time.
        # Use chunked reads instead of readline() to avoid asyncio's default
        # 64KB StreamReader buffer limit (LimitOverrunError). Claude CLI can
        # emit JSON lines >64KB when tool results contain large content.
        with open(stream_file, "w") as sf:
            buf = b""
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    if buf:
                        sf.write(buf.decode(errors="replace"))
                        sf.flush()
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    sf.write(line.decode(errors="replace") + "\n")
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

    if exit_code != 0 and stderr_content:
        logger.warning("%s stderr: %s", label, stderr_content[:500])

    logger.info("%s exited with code %d", label, exit_code)

    # --- Rate limit detection (3-step) ---
    rate_limited = False
    rate_limit_reason = ""

    # Check 1: Scan stderr for rate limit indicators
    if stderr_content:
        found, reason = detect_rate_limit_in_text(stderr_content)
        if found:
            rate_limited = True
            rate_limit_reason = f"stderr: {reason}"
            logger.warning("%s RATE LIMITED (stderr): %s", label, reason)

    # Check 2: Scan stream file for rate limit error events
    if not rate_limited:
        found, reason = _check_stream_for_rate_limits(stream_file)
        if found:
            rate_limited = True
            rate_limit_reason = f"stream: {reason}"
            logger.warning("%s RATE LIMITED (stream): %s", label, reason)

    # Check 3: Non-zero exit code with minimal output suggests rate limiting
    if not rate_limited and exit_code in RATE_LIMIT_EXIT_CODES:
        try:
            stream_size = Path(stream_file).stat().st_size
        except OSError:
            stream_size = 0
        if stream_size < 1024:
            rate_limited = True
            rate_limit_reason = (
                f"Exit code {exit_code} with minimal output "
                f"({stream_size} bytes) suggests plan exhaustion"
            )
            logger.warning("%s RATE LIMITED (exit code): %s", label, rate_limit_reason)

    return EmployeeResult(
        exit_code=exit_code,
        stream_file=stream_file,
        rate_limited=rate_limited,
        rate_limit_reason=rate_limit_reason,
        stderr_snippet=stderr_content[:500] if stderr_content else "",
        stdout_snippet="",
    )


async def run_employee_plan_phase(
    task: Task,
    config: CoordinatorConfig,
    employee_index: int,
    revision_feedback: str | None = None,
) -> EmployeeResult:
    """Spawn employee in plan-only mode.

    Returns EmployeeResult. The plan is written to
    .claude-employee-plan-{employee_index}.json in the workspace.
    """
    prompt = _build_plan_prompt(task, config, employee_index, revision_feedback)
    stream_file = _get_stream_file(config, task.project_repo, employee_index)
    stream_file = stream_file.replace(".stream.jsonl", "-plan.stream.jsonl")

    model = config.employee_model
    fallback_model = (
        "claude-sonnet-4-6"
        if model != "claude-sonnet-4-6"
        else "claude-haiku-4-5-20251001"
    )

    env = os.environ.copy()
    env["GITHUB_REPO"] = task.project_repo

    return await _run_claude_subprocess(
        prompt=prompt,
        system_prompt_file=str(PROMPTS_DIR / "employee.md"),
        model=model,
        fallback_model=fallback_model,
        max_turns=min(20, config.max_employee_turns),
        stream_file=stream_file,
        cwd=task.workspace,
        env=env,
        label=f"employee-{employee_index}-plan",
    )


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
    approved_plan: dict | None = None,
) -> EmployeeResult:
    """Spawn a Claude employee subprocess for a task.

    Returns an EmployeeResult with exit code, stream file path,
    and rate limit detection information.
    """
    if approved_plan is not None:
        prompt = _build_implement_with_plan_prompt(task, config, employee_index, approved_plan)
    else:
        prompt = _build_employee_prompt(task, config, employee_index)

    stream_file = _get_stream_file(config, task.project_repo, employee_index)

    # Calculate per-employee turn budget
    max_turns = config.max_employee_turns
    running_count = max(1, config.max_concurrent)
    if running_count > 1:
        max_turns = max(50, max_turns // running_count)

    model = config.employee_model
    fallback_model = "claude-sonnet-4-6" if model != "claude-sonnet-4-6" else "claude-haiku-4-5-20251001"

    env = os.environ.copy()
    env["GITHUB_REPO"] = task.project_repo

    return await _run_claude_subprocess(
        prompt=prompt,
        system_prompt_file=str(PROMPTS_DIR / "employee.md"),
        model=model,
        fallback_model=fallback_model,
        max_turns=max_turns,
        stream_file=stream_file,
        cwd=task.workspace,
        env=env,
        label=f"employee-{employee_index}",
    )
