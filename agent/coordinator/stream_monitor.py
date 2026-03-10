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


def _process_stream_event(event: dict, activity: EmployeeActivity, task: Task) -> None:
    """Process a single stream event and update activity tracking."""
    event_type = event.get("type", "")

    if event_type == "tool_use":
        tool_name = event.get("name", "")
        tool_input = event.get("input", {})
        activity.tool_calls += 1
        activity.last_tool = tool_name

        file_path = _extract_file_path(tool_name, tool_input)
        if file_path:
            activity.files_touched.add(file_path)
            activity.last_file = file_path
            if file_path not in task.touched_files:
                task.touched_files.append(file_path)

        # Track test runs
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if _is_test_command(command):
                activity.consecutive_test_failures = 0  # Reset, will increment on failure

    elif event_type == "tool_result":
        if activity.last_tool == "Bash" and _detect_test_failure(event):
            activity.test_failures += 1
            activity.consecutive_test_failures += 1

        if event.get("is_error"):
            error_content = str(event.get("content", ""))[:200]
            activity.errors.append(error_content)

    elif event_type == "result":
        # Employee finished
        pass


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
