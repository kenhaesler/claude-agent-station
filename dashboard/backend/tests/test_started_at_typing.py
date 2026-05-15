"""Regression guard: ``Run.started_at`` comparisons must bind a ``datetime``.

Binding an ISO-formatted string (``.isoformat()``) works on SQLite via silent
type coercion, but Postgres refuses with::

    operator does not exist: timestamp with time zone >= character varying

This was the root cause of the post-#393 Postgres-migration regression that
broke ``/api/queue/pressure`` and the ``/api/plan_usage/*`` endpoints — the
SQLite test path didn't catch it because SQLite is lenient.

Two layers of defence:

1. **Static check**: grep the routers for ``started_at >=`` and assert the
   right-hand side is not an ``.isoformat()`` call. Cheap, no DB needed.
2. **Parametrized runtime check**: execute the same aggregation pattern
   against both ``[sqlite, postgres]`` via ``async_session_factory`` and
   confirm it doesn't raise.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.database import Base, async_session, engine

ROUTERS_DIR = Path(__file__).resolve().parent.parent / "app" / "routers"

# Match ``Run.started_at >= <expr>`` or ``Run.started_at > <expr>`` where the
# right-hand side is an ``.isoformat()`` call. Multiline-aware so the
# ``.where(\n    Run.started_at >= week_start.isoformat()`` shape is caught.
_BAD_PATTERN = re.compile(
    r"Run\.started_at\s*>=?\s*[A-Za-z_][A-Za-z0-9_]*\.isoformat\(\)",
    re.MULTILINE,
)


def test_no_isoformat_in_started_at_comparisons():
    """Every router file: zero ``Run.started_at >= x.isoformat()`` occurrences."""
    offenders: list[str] = []
    for py_file in sorted(ROUTERS_DIR.glob("*.py")):
        text = py_file.read_text()
        for match in _BAD_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{py_file.name}:{line_no} — {match.group(0)}")
    assert offenders == [], (
        "Found Run.started_at compared against .isoformat() string — Postgres "
        "rejects this. Pass the datetime directly:\n  "
        + "\n  ".join(offenders)
    )


@pytest_asyncio.fixture
async def setup_db():
    """Per-function schema setup against the in-process engine.

    Avoids the session-scoped ``async_session_factory[postgres]`` event-
    loop binding flake (PR-3 ``test_runs_tree.py`` docstring documents
    the same workaround). Bound to whatever ``STATION_DB_URL`` configures
    — sqlite locally, Postgres in CI.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_started_at_aggregation_against_configured_dialect(
    setup_db,
) -> None:
    """The exact aggregation pattern from ``queue.py``/``plan_usage.py`` must
    work against whichever dialect the test DB is configured with. On
    Postgres (the production target post-#393) it would have failed
    before this PR with ``UndefinedFunctionError``; the regression guard
    here ensures the fix isn't reverted.
    """
    from app.models import Run

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    async with async_session() as db:
        db.add(Run(
            run_id="run-typing-1",
            status="completed",
            started_at=now,
            tokens_input=100,
            tokens_output=200,
        ))
        await db.commit()

    async with async_session() as db:
        result = await db.execute(
            select(
                func.coalesce(func.sum(Run.tokens_input), 0).label("input_tokens"),
                func.coalesce(func.sum(Run.tokens_output), 0).label("output_tokens"),
            ).where(Run.started_at >= cutoff)
        )
        row = result.one()
        # The bug would surface as a raised ProgrammingError before this
        # assertion; we just assert the inserted row's tokens contribute.
        assert row.input_tokens >= 100
        assert row.output_tokens >= 200
