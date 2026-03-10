"""Task DAG data structures and topological scheduling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """A single unit of work in the task DAG."""

    id: str
    run_id: str
    project_repo: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    issue_number: Optional[int] = None
    employee_index: Optional[int] = None
    depends_on: list[str] = field(default_factory=list)
    workspace: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    # Files this task is expected to touch (from decomposition)
    expected_files: list[str] = field(default_factory=list)
    # Files actually touched during execution (from stream monitor)
    touched_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "project_repo": self.project_repo,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "issue_number": self.issue_number,
            "employee_index": self.employee_index,
            "depends_on": self.depends_on,
            "workspace": self.workspace,
            "expected_files": self.expected_files,
            "touched_files": self.touched_files,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "exit_code": self.exit_code,
            "error_message": self.error_message,
        }


class TaskDAG:
    """Directed Acyclic Graph of tasks with dependency tracking."""

    def __init__(self, run_id: str, project_repo: str):
        self.run_id = run_id
        self.project_repo = project_repo
        self.tasks: dict[str, Task] = {}
        self._next_seq = 0

    def add_task(
        self,
        title: str,
        description: str = "",
        depends_on: list[str] | None = None,
        issue_number: int | None = None,
        expected_files: list[str] | None = None,
    ) -> Task:
        """Add a task to the DAG. Returns the created task."""
        task_id = f"task-{self.run_id}-{self._next_seq}"
        self._next_seq += 1

        task = Task(
            id=task_id,
            run_id=self.run_id,
            project_repo=self.project_repo,
            title=title,
            description=description,
            depends_on=depends_on or [],
            issue_number=issue_number,
            expected_files=expected_files or [],
            created_at=datetime.now(timezone.utc),
        )
        self.tasks[task_id] = task
        self._update_readiness()
        return task

    def ready_tasks(self) -> list[Task]:
        """Return tasks whose dependencies are all completed."""
        return [t for t in self.tasks.values() if t.status == TaskStatus.READY]

    def running_tasks(self) -> list[Task]:
        """Return currently running tasks."""
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]

    def mark_running(self, task_id: str, employee_index: int, workspace: str) -> None:
        """Mark a task as running with assigned employee."""
        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.employee_index = employee_index
        task.workspace = workspace
        task.started_at = datetime.now(timezone.utc)

    def mark_completed(self, task_id: str, exit_code: int = 0) -> list[Task]:
        """Mark a task as completed. Returns newly-unblocked tasks."""
        task = self.tasks[task_id]
        task.status = TaskStatus.COMPLETED
        task.exit_code = exit_code
        task.finished_at = datetime.now(timezone.utc)
        self._update_readiness()
        return self.ready_tasks()

    def mark_failed(self, task_id: str, error: str = "", exit_code: int = 1) -> list[Task]:
        """Mark a task as failed. Blocks dependents."""
        task = self.tasks[task_id]
        task.status = TaskStatus.FAILED
        task.exit_code = exit_code
        task.error_message = error
        task.finished_at = datetime.now(timezone.utc)
        self._cascade_block(task_id)
        return self.ready_tasks()

    def all_done(self) -> bool:
        """True if no tasks are pending, ready, or running."""
        active = {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING}
        return not any(t.status in active for t in self.tasks.values())

    def summary(self) -> dict[str, int]:
        """Count tasks by status."""
        counts: dict[str, int] = {}
        for t in self.tasks.values():
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        return counts

    def to_dict(self) -> dict:
        """Serialize the full DAG."""
        return {
            "run_id": self.run_id,
            "project_repo": self.project_repo,
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "summary": self.summary(),
        }

    def _update_readiness(self) -> None:
        """Update PENDING tasks to READY if all dependencies are met."""
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if all(
                self.tasks[dep].status == TaskStatus.COMPLETED
                for dep in task.depends_on
                if dep in self.tasks
            ):
                task.status = TaskStatus.READY

    def _cascade_block(self, failed_id: str) -> None:
        """Block all tasks that transitively depend on a failed task."""
        to_block: set[str] = set()
        queue = [failed_id]
        while queue:
            current = queue.pop()
            for task in self.tasks.values():
                if task.id in to_block:
                    continue
                if current in task.depends_on and task.status in (
                    TaskStatus.PENDING,
                    TaskStatus.READY,
                ):
                    task.status = TaskStatus.BLOCKED
                    task.error_message = f"Blocked by failed task: {failed_id}"
                    to_block.add(task.id)
                    queue.append(task.id)

    @classmethod
    def single_task(cls, run_id: str, project_repo: str, title: str, description: str = "", issue_number: int | None = None) -> TaskDAG:
        """Create a trivial DAG with a single task (fallback when decomposition is skipped)."""
        dag = cls(run_id, project_repo)
        dag.add_task(title=title, description=description, issue_number=issue_number)
        return dag
