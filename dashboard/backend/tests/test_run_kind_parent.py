"""Run.run_kind / parent_run_id / split_decision_json columns (#391).

Why these columns matter for the splitter (#391):

- ``run_kind`` distinguishes the three run shapes the splitter pipeline
  produces: ``primary`` (a normal single-issue run), ``sub-of-<N>``
  (a sub-run spawned from parent issue #N), and ``split-decision``
  (the meta-run whose only output is the proposal set + integration
  branch — no code change).
- ``parent_run_id`` is the FK the tree-view endpoint scans by;
  indexing is non-negotiable because the typical query is
  ``WHERE parent_run_id = :id`` issued live from the dashboard.
- ``split_decision_json`` archives the decision payload alongside the
  run row so operators can replay the splitter's reasoning even after
  GitHub history rotates.

Uses the ``app.database.engine`` + ``Base.metadata.create_all`` pattern
shared with the other model tests (e.g. ``test_audit_log.py``) rather
than the session-scoped ``async_session_factory``; the session-scoped
engine binds connections to its initial event loop and breaks when
multiple tests target ``async_session_factory[postgres]`` in the same
pytest run (pre-existing infrastructure limitation).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, inspect as sa_inspect

from app.database import Base, async_session, engine
from app.models import Run


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_run_kind_parent_columns(setup_db):
    async with async_session() as db:
        db.add(Run(run_id="run-parent-1", run_kind="primary",
                   started_at=datetime.now(timezone.utc),
                   split_decision_json={"proposals": 4}))
        db.add(Run(run_id="run-sub-a", run_kind="sub-of-27",
                   parent_run_id="run-parent-1",
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    async with async_session() as db:
        sub = (await db.execute(select(Run).where(Run.run_id == "run-sub-a"))).scalar_one()
        assert sub.run_kind == "sub-of-27"
        assert sub.parent_run_id == "run-parent-1"
        parent = (await db.execute(select(Run).where(Run.run_id == "run-parent-1"))).scalar_one()
        assert parent.split_decision_json == {"proposals": 4}


@pytest.mark.asyncio
async def test_parent_run_id_indexed(setup_db):
    """The tree-view endpoint scans by ``parent_run_id`` — without an
    index that becomes a full table scan on every dashboard render.
    """
    def _check(sync_conn):
        insp = sa_inspect(sync_conn)
        idx_columns = {tuple(ix["column_names"]) for ix in insp.get_indexes("runs")}
        assert ("parent_run_id",) in idx_columns

    async with engine.begin() as conn:
        await conn.run_sync(_check)
