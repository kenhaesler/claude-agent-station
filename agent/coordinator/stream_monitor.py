"""Real-time monitoring of employee .stream.jsonl files."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import Task

logger = logging.getLogger(__name__)


@dataclass
class EmployeeActivity:
    """Tracks what an employee is currently doing."""

    employee_index: int
    task_id: str
    files_touched: set[str] = field(default_factory=set)
    tool_calls: int = 0
    test_failures: int = 0
    consecutive_test_failures: int = 0
    last_tool: str = ""
    last_file: str = ""
    errors: list[str] = field(default_factory=list)
    MAX_ERRORS: int = field(default=100, repr=False)

    # Stuck detection fields
    is_stuck: bool = False
    stuck_reason: str = ""
    same_file_edit_streak: int = 0
    last_edited_file: str = ""
    tests_without_code_change: int = 0
    no_progress_since: int = 0
    has_committed: bool = False


def _extract_file_path(tool_name: str, tool_input: dict) -> str | None:
    """Extract the file path from a tool_use event."""
    if tool_name in ("Write", "Read", "Edit"):
        return tool_input.get("file_path") or tool_input.get("path")
    if tool_name == "Glob":
        return tool_input.get("path")
    return None


def _is_test_command(command: str) -> bool:
    """Check if a bash command is running tests."""
    test_keywords = ["test", "pytest", "jest", "vitest", "mocha", "cargo test", "go test"]
    return any(kw in command.lower() for kw in test_keywords)


def _detect_test_failure(tool_result: dict) -> bool:
    """Check if a tool result indicates test failure."""
    content = str(tool_result.get("content", ""))
    # Non-zero exit code in bash
    if tool_result.get("is_error"):
        return True
    fail_markers = ["FAILED", "FAIL", "Error:", "AssertionError", "test failed"]
    return any(m in content for m in fail_markers)


async def monitor_stream(
    stream_file: str,
    task: Task,
    activity: EmployeeActivity,
    config: CoordinatorConfig,
    stop_event: asyncio.Event,
) -> None:
    """Tail a .stream.jsonl file and update activity tracking.

    Runs until stop_event is set or the stream file indicates completion.
    """
    position = 0
    path = Path(stream_file)

    while not stop_event.is_set():
        if not path.exists():
            await asyncio.sleep(config.stream_poll_interval)
            continue

        try:
            with open(path, "r") as f:
                f.seek(position)
                new_data = f.read()
                position = f.tell()
        except OSError:
            await asyncio.sleep(config.stream_poll_interval)
            continue

        if not new_data:
            await asyncio.sleep(config.stream_poll_interval)
            continue

        for line in new_data.strip().split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            _process_stream_event(event, activity, task)

        await asyncio.sleep(config.stream_poll_interval)


def _handle_tool_use(tool_name: str, tool_input: dict, activity: EmployeeActivity, task: Task) -> None:
    """Process a tool_use block and update activity tracking."""
    activity.tool_calls += 1
    activity.last_tool = tool_name

    file_path = _extract_file_path(tool_name, tool_input)
    if file_path:
        activity.files_touched.add(file_path)
        activity.last_file = file_path
        if file_path not in task.touched_files and len(task.touched_files) < 500:
            task.touched_files.append(file_path)

    # Track test runs and commits
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if _is_test_command(command):
            # Don't reset consecutive_test_failures here — reset on success in tool_result
            activity.tests_without_code_change += 1
        elif "git commit" in command or "git add" in command:
            activity.has_committed = True
            activity.no_progress_since = 0
        else:
            activity.tests_without_code_change = 0

    # Stuck detection: same file edit streak
    if tool_name == "Edit":
        if file_path and file_path == activity.last_edited_file:
            activity.same_file_edit_streak += 1
        else:
            activity.same_file_edit_streak = 1  # First edit counts as 1
        if file_path:
            activity.last_edited_file = file_path
            activity.tests_without_code_change = 0  # Code change resets test counter

    # Stuck pattern 1: Editing same file 6+ times without testing
    if activity.same_file_edit_streak >= 6:
        activity.is_stuck = True
        activity.stuck_reason = (
            f"Edited {activity.last_edited_file} {activity.same_file_edit_streak}x "
            f"without testing"
        )


def _handle_tool_result(event: dict, activity: EmployeeActivity) -> None:
    """Process a tool_result block and update activity tracking."""
    if activity.last_tool == "Bash":
        if _detect_test_failure(event):
            activity.test_failures += 1
            activity.consecutive_test_failures += 1

            # Stuck pattern 2: 3+ consecutive test failures
            if activity.consecutive_test_failures >= 3:
                activity.is_stuck = True
                activity.stuck_reason = (
                    f"{activity.consecutive_test_failures} consecutive test failures"
                )
        elif not event.get("is_error"):
            # Test passed — reset consecutive failure counter
            activity.consecutive_test_failures = 0

    if event.get("is_error"):
        error_content = str(event.get("content", ""))[:200]
        if len(activity.errors) < activity.MAX_ERRORS:
            activity.errors.append(error_content)

    # Stuck pattern 3: Running tests repeatedly without code changes
    if activity.tests_without_code_change >= 3:
        activity.is_stuck = True
        activity.stuck_reason = "Running tests repeatedly without code changes"


def _process_stream_event(event: dict, activity: EmployeeActivity, task: Task) -> None:
    """Process a single stream event and update activity tracking."""
    event_type = event.get("type", "")

    # Claude CLI stream-json format: tool_use/tool_result are nested inside
    # assistant/user message wrappers in message.content[] blocks.
    if event_type == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                _handle_tool_use(block.get("name", ""), block.get("input", {}), activity, task)
        return

    if event_type == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                _handle_tool_result(block, activity)
        return

    # Legacy top-level format (backward compat)
    if event_type == "tool_use":
        _handle_tool_use(event.get("name", ""), event.get("input", {}), activity, task)
        return

    if event_type == "tool_result":
        _handle_tool_result(event, activity)
        return

    if event_type == "result":
        # Employee finished
        pass

    # Stuck pattern 4: No progress after 100 tool calls without a commit
    if activity.tool_calls > 100 and not activity.has_committed:
        activity.no_progress_since += 1
        if activity.no_progress_since >= 20:  # Check every ~20 events after threshold
            activity.is_stuck = True
            activity.stuck_reason = (
                f"{activity.tool_calls} tool calls without a commit"
            )


class ConflictDetector:
    """Detect when multiple employees are editing the same files."""

    def __init__(self):
        self._employee_files: dict[int, set[str]] = {}

    def update(self, employee_index: int, files: set[str]) -> None:
        """Update the file set for an employee."""
        self._employee_files[employee_index] = files

    def check_conflicts(self, employee_index: int) -> list[tuple[str, int]]:
        """Check if this employee's files conflict with any other employee.

        Returns list of (file_path, other_employee_index) conflicts.
        """
        my_files = self._employee_files.get(employee_index, set())
        conflicts = []
        for other_idx, other_files in self._employee_files.items():
            if other_idx == employee_index:
                continue
            overlap = my_files & other_files
            for f in overlap:
                conflicts.append((f, other_idx))
        return conflicts

    def clear(self, employee_index: int) -> None:
        """Remove tracking for an employee that has finished."""
        self._employee_files.pop(employee_index, None)
