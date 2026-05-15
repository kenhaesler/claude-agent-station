"""Per-project runner-resource columns (#386)."""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_project_runner_quota_columns(async_session_factory):
    from app.models import Project

    async with async_session_factory() as db:
        p = Project(
            repo="x/y",
            branch="main",
            runner_memory_limit=536870912,  # 512 MiB in bytes
            runner_cpu_limit=0.5,
        )
        db.add(p)
        await db.commit()

    async with async_session_factory() as db:
        row = (await db.execute(select(Project).where(Project.repo == "x/y"))).scalar_one()
        assert row.runner_memory_limit == 536870912
        assert row.runner_cpu_limit == 0.5
