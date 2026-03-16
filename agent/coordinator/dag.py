"""Task DAG data structures and topological scheduling — SQLite-backed."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.coordinator.db import DbCoordinatorTask


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """Read-only DTO constructed from DB rows."""

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
    expected_files: list[str] = field(default_factory=list)
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


def _row_to_task(row: DbCoordinatorTask) -> Task:
    """Convert a DB row to a Task DTO."""
    return Task(
        id=row.id,
        run_id=row.run_id,
        project_repo=row.project_repo,
        title=row.title,
        description=row.description or "",
        status=TaskStatus(row.status),
        issue_number=row.issue_number,
        employee_index=row.employee_index,
        depends_on=json.loads(row.depends_on) if row.depends_on else [],
        workspace=row.workspace,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        exit_code=row.exit_code,
        error_message=row.error_message,
        expected_files=json.loads(row.expected_files) if row.expected_files else [],
        touched_files=json.loads(row.touched_files) if row.touched_files else [],
    )


class TaskDAG:
    """Directed Acyclic Graph of tasks — backed by SQLite."""

    def __init__(
        self,
        run_id: str,
        project_repo: str,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.run_id = run_id
        self.project_repo = project_repo
        self._sf = session_factory

    async def add_task(
        self,
        title: str,
        description: str = "",
        depends_on: list[str] | None = None,
        issue_number: int | None = None,
        expected_files: list[str] | None = None,
    ) -> Task:
        """Insert a task into the DB. Returns the created Task DTO."""
        async with self._sf() as session:
            seq = await self._next_seq(session)
            task_id = f"task-{self.run_id}-{seq}"
            now = datetime.now(timezone.utc)

            row = DbCoordinatorTask(
                id=task_id,
                run_id=self.run_id,
                project_repo=self.project_repo,
                title=title,
                description=description,
                depends_on=json.dumps(depends_on or []),
                issue_number=issue_number,
                expected_files=json.dumps(expected_files or []),
                status=TaskStatus.PENDING.value,
                created_at=now,
            )
            session.add(row)
            await session.commit()

        await self._update_readiness()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> Task:
        """Fetch a single task by ID."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(DbCoordinatorTask.id == task_id)
            )
            row = result.scalar_one()
            return _row_to_task(row)

    async def ready_tasks(self) -> list[Task]:
        """Return tasks whose status is READY."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                    DbCoordinatorTask.status == TaskStatus.READY.value,
                )
            )
            return [_row_to_task(r) for r in result.scalars().all()]

    async def running_tasks(self) -> list[Task]:
        """Return currently running tasks."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                    DbCoordinatorTask.status == TaskStatus.RUNNING.value,
                )
            )
            return [_row_to_task(r) for r in result.scalars().all()]

    async def mark_running(self, task_id: str, employee_index: int, workspace: str) -> None:
        """Mark a task as running with assigned employee."""
        async with self._sf() as session:
            await session.execute(
                update(DbCoordinatorTask)
                .where(DbCoordinatorTask.id == task_id)
                .values(
                    status=TaskStatus.RUNNING.value,
                    employee_index=employee_index,
                    workspace=workspace,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def mark_completed(self, task_id: str, exit_code: int = 0) -> list[Task]:
        """Mark a task as completed. Returns newly-ready tasks."""
        async with self._sf() as session:
            await session.execute(
                update(DbCoordinatorTask)
                .where(DbCoordinatorTask.id == task_id)
                .values(
                    status=TaskStatus.COMPLETED.value,
                    exit_code=exit_code,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        await self._update_readiness()
        return await self.ready_tasks()

    async def mark_failed(self, task_id: str, error: str = "", exit_code: int = 1) -> list[Task]:
        """Mark a task as failed. Blocks dependents."""
        async with self._sf() as session:
            await session.execute(
                update(DbCoordinatorTask)
                .where(DbCoordinatorTask.id == task_id)
                .values(
                    status=TaskStatus.FAILED.value,
                    exit_code=exit_code,
                    error_message=error,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        await self._cascade_block(task_id)
        return await self.ready_tasks()

    async def all_done(self) -> bool:
        """True if no tasks are pending, ready, or running."""
        active = [TaskStatus.PENDING.value, TaskStatus.READY.value, TaskStatus.RUNNING.value]
        async with self._sf() as session:
            result = await session.execute(
                select(func.count()).select_from(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                    DbCoordinatorTask.status.in_(active),
                )
            )
            return result.scalar_one() == 0

    async def summary(self) -> dict[str, int]:
        """Count tasks by status."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask.status, func.count())
                .where(DbCoordinatorTask.run_id == self.run_id)
                .group_by(DbCoordinatorTask.status)
            )
            return {status: count for status, count in result.all()}

    async def task_count(self) -> int:
        """Total number of tasks for this run."""
        async with self._sf() as session:
            result = await session.execute(
                select(func.count()).select_from(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                )
            )
            return result.scalar_one()

    async def to_dict(self) -> dict:
        """Serialize the full DAG."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                )
            )
            tasks = [_row_to_task(r) for r in result.scalars().all()]
        return {
            "run_id": self.run_id,
            "project_repo": self.project_repo,
            "tasks": [t.to_dict() for t in tasks],
            "summary": await self.summary(),
        }

    async def update_touched_files(self, task_id: str, files: list[str]) -> None:
        """Update the touched_files list for a completed task."""
        async with self._sf() as session:
            await session.execute(
                update(DbCoordinatorTask)
                .where(DbCoordinatorTask.id == task_id)
                .values(touched_files=json.dumps(files))
            )
            await session.commit()

    async def recover_from_crash(self) -> int:
        """Reset RUNNING tasks to READY for crash recovery. Returns count reset."""
        async with self._sf() as session:
            result = await session.execute(
                update(DbCoordinatorTask)
                .where(
                    DbCoordinatorTask.run_id == self.run_id,
                    DbCoordinatorTask.status == TaskStatus.RUNNING.value,
                )
                .values(
                    status=TaskStatus.READY.value,
                    employee_index=None,
                    workspace=None,
                    started_at=None,
                )
            )
            await session.commit()
            return result.rowcount

    async def has_cycle(self) -> bool:
        """Detect cycles via DFS. Called after adding tasks to prevent scheduler hangs."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                )
            )
            rows = result.scalars().all()

        adj: dict[str, list[str]] = {
            r.id: json.loads(r.depends_on) if r.depends_on else []
            for r in rows
        }
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            in_stack.add(node)
            for dep in adj.get(node, []):
                if dep in in_stack:
                    return True
                if dep not in visited and dfs(dep):
                    return True
            in_stack.discard(node)
            return False

        return any(dfs(n) for n in adj if n not in visited)

    # -- internal helpers --

    async def _next_seq(self, session: AsyncSession) -> int:
        """Get next sequence number for this run."""
        result = await session.execute(
            select(func.count()).select_from(DbCoordinatorTask).where(
                DbCoordinatorTask.run_id == self.run_id,
            )
        )
        return result.scalar_one()

    async def _update_readiness(self) -> None:
        """Update PENDING tasks to READY if all dependencies are met."""
        async with self._sf() as session:
            # Fetch all tasks for this run
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                )
            )
            rows = result.scalars().all()

            # Build status lookup
            status_map = {r.id: r.status for r in rows}

            # Find PENDING tasks whose deps are all COMPLETED
            to_ready: list[str] = []
            for row in rows:
                if row.status != TaskStatus.PENDING.value:
                    continue
                deps = json.loads(row.depends_on) if row.depends_on else []
                if all(
                    status_map.get(dep) == TaskStatus.COMPLETED.value
                    for dep in deps
                    if dep in status_map
                ):
                    to_ready.append(row.id)

            if to_ready:
                await session.execute(
                    update(DbCoordinatorTask)
                    .where(DbCoordinatorTask.id.in_(to_ready))
                    .values(status=TaskStatus.READY.value)
                )
                await session.commit()

    async def _cascade_block(self, failed_id: str) -> None:
        """Block all tasks that transitively depend on a failed task."""
        async with self._sf() as session:
            result = await session.execute(
                select(DbCoordinatorTask).where(
                    DbCoordinatorTask.run_id == self.run_id,
                )
            )
            rows = {r.id: r for r in result.scalars().all()}

            to_block: set[str] = set()
            queue = [failed_id]
            while queue:
                current = queue.pop()
                for row in rows.values():
                    if row.id in to_block:
                        continue
                    deps = json.loads(row.depends_on) if row.depends_on else []
                    if current in deps and row.status in (
                        TaskStatus.PENDING.value,
                        TaskStatus.READY.value,
                    ):
                        to_block.add(row.id)
                        queue.append(row.id)

            if to_block:
                await session.execute(
                    update(DbCoordinatorTask)
                    .where(DbCoordinatorTask.id.in_(list(to_block)))
                    .values(
                        status=TaskStatus.BLOCKED.value,
                        error_message=f"Blocked by failed task: {failed_id}",
                    )
                )
                await session.commit()

    @classmethod
    async def single_task(
        cls,
        run_id: str,
        project_repo: str,
        session_factory: async_sessionmaker[AsyncSession],
        title: str,
        description: str = "",
        issue_number: int | None = None,
    ) -> TaskDAG:
        """Create a trivial DAG with a single task."""
        dag = cls(run_id, project_repo, session_factory)
        await dag.add_task(title=title, description=description, issue_number=issue_number)
        return dag
