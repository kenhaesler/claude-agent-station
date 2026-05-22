"""Regression test for the runs.autonomy_level backfill in alembic 0006.

The backfill SQL must, for every ``runs`` row whose autonomy_level is NULL
or the stale ``'assisted'`` default, copy the owning project's current
``autonomy_level``. Rows already carrying ``'auto'`` or ``'manual'``
(deliberate per-run overrides) are left alone, as are rows with
``project_id IS NULL``.

We exercise the bare SQL statement against an in-memory SQLite DB rather
than booting the full alembic stack — the migration's only non-trivial
piece is this UPDATE, and running it directly keeps the test fast and
focused.
"""
from __future__ import annotations

import re
from pathlib import Path

import sqlite3

import pytest


BACKFILL_SQL = """
UPDATE runs
SET autonomy_level = (
    SELECT projects.autonomy_level
    FROM projects
    WHERE projects.id = runs.project_id
)
WHERE runs.project_id IS NOT NULL
  AND (runs.autonomy_level IS NULL OR runs.autonomy_level = 'assisted')
  AND EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = runs.project_id
        AND projects.autonomy_level IS NOT NULL
  )
"""


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            autonomy_level TEXT
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            project_id INTEGER,
            autonomy_level TEXT
        );
        """
    )
    return conn


def _autonomy(conn: sqlite3.Connection, run_id: str) -> str | None:
    cur = conn.execute(
        "SELECT autonomy_level FROM runs WHERE run_id = ?", (run_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def test_backfill_promotes_auto_project_runs(db: sqlite3.Connection) -> None:
    """A run whose project is FULL AUTO must end up labelled 'auto'."""
    db.execute("INSERT INTO projects (id, autonomy_level) VALUES (1, 'auto')")
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-1', 1, 'assisted')"
    )
    db.execute(BACKFILL_SQL)

    assert _autonomy(db, "run-1") == "auto"


def test_backfill_fills_null_runs_from_project(db: sqlite3.Connection) -> None:
    """A run with NULL autonomy_level adopts its project's setting."""
    db.execute("INSERT INTO projects (id, autonomy_level) VALUES (2, 'manual')")
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-2', 2, NULL)"
    )
    db.execute(BACKFILL_SQL)

    assert _autonomy(db, "run-2") == "manual"


def test_backfill_leaves_deliberate_overrides_alone(db: sqlite3.Connection) -> None:
    """A run carrying 'auto' or 'manual' must not be reset to project default."""
    db.execute("INSERT INTO projects (id, autonomy_level) VALUES (3, 'assisted')")
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-3-auto', 3, 'auto')"
    )
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-3-manual', 3, 'manual')"
    )
    db.execute(BACKFILL_SQL)

    assert _autonomy(db, "run-3-auto") == "auto"
    assert _autonomy(db, "run-3-manual") == "manual"


def test_backfill_skips_orphaned_runs(db: sqlite3.Connection) -> None:
    """A run with NULL project_id stays NULL — there's no project to copy from."""
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-orphan', NULL, NULL)"
    )
    db.execute(BACKFILL_SQL)

    assert _autonomy(db, "run-orphan") is None


def test_backfill_skips_when_project_autonomy_unknown(db: sqlite3.Connection) -> None:
    """If a project itself has NULL autonomy_level, we don't overwrite the run."""
    db.execute("INSERT INTO projects (id, autonomy_level) VALUES (4, NULL)")
    db.execute(
        "INSERT INTO runs (run_id, project_id, autonomy_level) "
        "VALUES ('run-4', 4, 'assisted')"
    )
    db.execute(BACKFILL_SQL)

    # 'assisted' stays — no project signal to upgrade or downgrade it.
    assert _autonomy(db, "run-4") == "assisted"


def test_backfill_sql_matches_migration_file() -> None:
    """The SQL block embedded in the migration must stay in sync with what
    this test exercises. Guards against silent drift if the migration is
    later edited without updating the test."""
    migration = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0006_run_autonomy_level.py"
    ).read_text()

    normalize = lambda s: re.sub(r"\s+", " ", s).strip()
    assert normalize(BACKFILL_SQL) in normalize(migration), (
        "Backfill SQL in 0006 migration drifted from the regression test"
    )
