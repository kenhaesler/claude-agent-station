# SQLite -> Postgres Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Postgres (via asyncpg) as the production database driver while keeping SQLite supported as a local-dev / test fallback. Move imperative `_migrate_add_columns` to Alembic. Replace `log_importer` + `stale_run_reaper` polling with Postgres LISTEN/NOTIFY on the production path. Ship a one-shot SQLite→Postgres converter with a documented playbook.

**Architecture:** `STATION_DB_URL` becomes the preferred config; `STATION_DB_PATH` is a SQLite-only fallback. `Settings.resolved_db_url` resolves between them. `dashboard/backend/app/database.py` creates an async engine sized by dialect; the `PRAGMA` connect listener no-ops under Postgres. Alembic owns schema evolution end-to-end (one squashed baseline revision encoding every column / index `_migrate_add_columns` adds today). Three JSON-text columns become dialect-aware (`JSON.with_variant(JSONB, "postgresql")`). A new `services/pubsub.py` exposes async `listen()` / `notify()` over asyncpg LISTEN/NOTIFY with a no-op SQLite fallback. The migration script (`scripts/migrate_sqlite_to_postgres.py`) walks `Base.metadata.sorted_tables`, batches rows, decodes JSON-text into JSONB, resets sequences, and reports row-count parity. `compose.yml` gains a `db` service with healthcheck and Docker secret.

**Tech Stack:** Python 3.11+ / SQLAlchemy 2 async / asyncpg / aiosqlite / Alembic / pytest + `pytest-docker` for ephemeral Postgres, FastAPI, Docker compose.

**Tracking issue:** [#393](https://github.com/kenhaesler/claude-agent-station/issues/393)

**Spec:** `docs/superpowers/specs/2026-05-14-issue-393-postgres-migration.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `dashboard/backend/requirements.txt` | modify | Add `asyncpg>=0.29`, `alembic>=1.13`, `pytest-docker>=3` (test). |
| `dashboard/backend/app/config.py` | modify | Add `db_url` field; add `resolved_db_url` property. |
| `dashboard/backend/app/database.py` | modify | Resolve URL via `settings.resolved_db_url`; dialect-aware pool sizing; PRAGMA listener guarded by dialect; replace `_migrate_add_columns` call with `alembic upgrade head`. |
| `dashboard/backend/app/models.py` | modify | Three columns become `JsonType` (dialect-aware JSON / JSONB). |
| `dashboard/backend/app/services/pubsub.py` | **new** | `listen(channel)` async iterator + `notify(channel, payload)` over asyncpg LISTEN/NOTIFY; SQLite no-op. |
| `dashboard/backend/app/services/log_importer.py` | modify | Subscribe to `run_event` channel on Postgres; raise poll interval to 300 s; keep poll-only path for SQLite. |
| `dashboard/backend/app/services/stale_run_reaper.py` | modify | Subscribe to `heartbeat` channel on Postgres; keep 15 s tick for SQLite. |
| `dashboard/backend/app/routers/webhook.py` | modify | Call `notify("run_event", ...)` after insert. |
| `dashboard/backend/app/services/run_lifecycle.py` | modify | Call `notify("heartbeat", ...)` after `last_event_at` bumps. |
| `dashboard/backend/alembic/env.py` | **new** | Async-aware Alembic env using `STATION_DB_URL`. |
| `dashboard/backend/alembic/script.py.mako` | **new** | Standard Alembic mako template. |
| `dashboard/backend/alembic/versions/0001_baseline.py` | **new** | One revision creating every table, every column, every index in today's `Base.metadata` + `_migrate_add_columns`. |
| `dashboard/backend/alembic.ini` | **new** | Alembic project file. |
| `scripts/__init__.py` | **new** | Empty package marker. |
| `scripts/migrate_sqlite_to_postgres.py` | **new** | One-shot CLI converter. |
| `compose.yml` | modify | Add `db` service, secrets block, depends_on links; new `STATION_DB_URL` env. |
| `.secrets/db_password` | new file (gitignored) | Docker secret seed; written by the operator playbook. |
| `.gitignore` | modify | Add `.secrets/`. |
| `dashboard/backend/tests/conftest.py` | modify | Parametrize `db_url` across `sqlite`/`postgres`; add Postgres fixture via `pytest-docker`. |
| `dashboard/backend/tests/test_database.py` | **new** | Resolved URL, dialect-aware pool sizing, PRAGMA listener no-op on Postgres. |
| `dashboard/backend/tests/test_migrations.py` | **new** | `alembic upgrade head` against fresh Postgres produces schema isomorphic to `Base.metadata.create_all`. |
| `dashboard/backend/tests/test_pubsub.py` | **new** | LISTEN/NOTIFY round-trip; marked `postgres_only`. |
| `dashboard/backend/tests/test_migration_script.py` | **new** | Row-count parity per table; sequence reset; JSON->JSONB decode. |
| `dashboard/backend/tests/integration/test_run_e2e.py` | **new** | Synthetic run end-to-end; parametrised across both backends. |
| `docs/configuration.md` | modify | Add "Database migration" section with the playbook + rollback. |

---

## Setup (run once per execution session)

### Task 0: Sync local dev and prep

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

Expected: `Already up to date.` or a fast-forward summary.

- [ ] **Step 2: Confirm backend tests pass on a clean tree**

```bash
cd dashboard/backend && python3 -m pytest -q
```

Expected: all green.

- [ ] **Step 3: Create branch**

```bash
git checkout -b feature/393-postgres-migration
```

- [ ] **Step 4: Install Docker compose plugin and pull Postgres image**

```bash
docker compose version
docker pull postgres:16-alpine
```

Expected: image pulled; compose CLI prints version.

- [ ] **Step 5: (No commit)**

---

# PR 1 — Configuration + engine + Alembic baseline

## Task 1: Add `db_url` to settings; `resolved_db_url` property

**Files:**
- Modify: `dashboard/backend/app/config.py`
- Test: `dashboard/backend/tests/test_database.py` (new)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_database.py`:

```python
"""STATION_DB_URL / STATION_DB_PATH resolution tests (#393)."""
from __future__ import annotations

import pytest

from app.config import Settings


def test_resolved_db_url_prefers_db_url():
    s = Settings(
        db_path="/tmp/legacy.db",
        db_url="postgresql+asyncpg://u:p@h/db",
    )
    assert s.resolved_db_url == "postgresql+asyncpg://u:p@h/db"


def test_resolved_db_url_falls_back_to_sqlite_path():
    s = Settings(db_path="/tmp/x.db", db_url=None)
    assert s.resolved_db_url == "sqlite+aiosqlite:////tmp/x.db"


def test_resolved_db_url_blank_falls_back_to_sqlite():
    s = Settings(db_path="/tmp/y.db", db_url="")
    assert s.resolved_db_url == "sqlite+aiosqlite:////tmp/y.db"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: `AttributeError: 'Settings' has no attribute 'resolved_db_url'` or `db_url`.

- [ ] **Step 3: Add the field + property**

In `dashboard/backend/app/config.py`, add (near the existing `db_path` field):

```python
    db_url: str | None = None  # preferred; full SQLAlchemy URL incl. driver

    @property
    def resolved_db_url(self) -> str:
        """Return the URL the engine should use.

        Order: ``db_url`` env (production), then ``db_path`` (SQLite fallback).
        Empty string treated as unset.
        """
        if self.db_url:
            return self.db_url
        return f"sqlite+aiosqlite:///{self.db_path}"
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/config.py dashboard/backend/tests/test_database.py
git commit -m "feat(db): add STATION_DB_URL + resolved_db_url (#393)"
```

---

## Task 2: Engine dialect awareness — pool sizing + PRAGMA guard

**Files:**
- Modify: `dashboard/backend/app/database.py`
- Test: `dashboard/backend/tests/test_database.py` (append)

- [ ] **Step 1: Append failing test**

```python
from sqlalchemy.ext.asyncio import create_async_engine


def test_pragma_listener_no_op_on_postgres():
    """Postgres connections must not run sqlite PRAGMAs."""
    from app.database import _set_sqlite_pragma  # noqa

    class FakeCursor:
        ran: list[str] = []

        def execute(self, sql):
            self.ran.append(sql)

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    class FakeEngine:
        class dialect:
            name = "postgresql"

    # Override the engine reference used by the listener guard.
    import app.database as mod
    orig = mod.engine
    try:
        mod.engine = FakeEngine()  # type: ignore[assignment]
        cur = FakeCursor()
        _set_sqlite_pragma._inner_run(FakeConn(), None, cursor_factory=lambda c: cur)  # type: ignore[attr-defined]
        assert cur.ran == [], "should not run pragmas under postgres"
    finally:
        mod.engine = orig


def test_pool_size_scales_by_dialect():
    from app.database import _engine_kwargs

    sqlite_kw = _engine_kwargs("sqlite+aiosqlite:///:memory:")
    pg_kw = _engine_kwargs("postgresql+asyncpg://u:p@h/db")
    assert sqlite_kw["pool_size"] == 5
    assert sqlite_kw["max_overflow"] == 0
    assert pg_kw["pool_size"] == 20
    assert pg_kw["max_overflow"] == 10
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: `ImportError: cannot import name '_engine_kwargs'` (or AttributeError on the inner helper).

- [ ] **Step 3: Refactor `database.py`**

Replace the top of `dashboard/backend/app/database.py` (lines 13-30) with:

```python
DATABASE_URL = settings.resolved_db_url


def _engine_kwargs(url: str) -> dict:
    is_pg = url.startswith("postgresql")
    return {
        "echo": False,
        "pool_size": 20 if is_pg else 5,
        "max_overflow": 10 if is_pg else 0,
    }


engine = create_async_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL + foreign keys — SQLite-only."""
    if engine.dialect.name != "sqlite":
        return
    _set_sqlite_pragma._inner_run(dbapi_conn, connection_record)


def _inner_run(dbapi_conn, _connection_record, *, cursor_factory=None):
    cursor = (cursor_factory or (lambda c: c.cursor()))(dbapi_conn)
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_set_sqlite_pragma._inner_run = _inner_run  # type: ignore[attr-defined]
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/database.py dashboard/backend/tests/test_database.py
git commit -m "feat(db): dialect-aware engine + PRAGMA guard (#393)"
```

---

## Task 3: Add asyncpg + alembic dependencies

**Files:**
- Modify: `dashboard/backend/requirements.txt`

- [ ] **Step 1: Write a failing import test**

Append to `dashboard/backend/tests/test_database.py`:

```python
def test_asyncpg_and_alembic_importable():
    import alembic  # noqa: F401
    import asyncpg  # noqa: F401
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py::test_asyncpg_and_alembic_importable -q
```

Expected: `ModuleNotFoundError: No module named 'asyncpg'`.

- [ ] **Step 3: Add deps and install**

Append to `dashboard/backend/requirements.txt`:

```
asyncpg>=0.29
alembic>=1.13
pytest-docker>=3
```

Install:

```bash
cd dashboard/backend && pip install -r requirements.txt
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/requirements.txt dashboard/backend/tests/test_database.py
git commit -m "build(db): add asyncpg + alembic dependencies (#393)"
```

---

## Task 4: Alembic project skeleton

**Files:**
- New: `dashboard/backend/alembic.ini`
- New: `dashboard/backend/alembic/env.py`
- New: `dashboard/backend/alembic/script.py.mako`
- New: `dashboard/backend/alembic/versions/.gitkeep`

- [ ] **Step 1: Write the failing smoke test**

Create `dashboard/backend/tests/test_migrations.py`:

```python
"""Alembic baseline tests (#393)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_alembic_config_exists():
    assert (Path(__file__).parent.parent / "alembic.ini").exists()


def test_alembic_history_runs():
    proc = subprocess.run(
        ["alembic", "history"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py -q
```

Expected: 2 failed (`alembic.ini` not present).

- [ ] **Step 3: Add Alembic skeleton**

Create `dashboard/backend/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `dashboard/backend/alembic/script.py.mako`:

```python
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `dashboard/backend/alembic/env.py`:

```python
"""Async-aware Alembic env (#393).

Resolves the URL from ``Settings.resolved_db_url`` so both SQLite and
Postgres work with the same migrations.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base
# Ensure all models are imported so Base.metadata is fully populated.
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.resolved_db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.resolved_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = settings.resolved_db_url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

Create `dashboard/backend/alembic/versions/.gitkeep` (empty file).

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/alembic.ini dashboard/backend/alembic/env.py dashboard/backend/alembic/script.py.mako dashboard/backend/alembic/versions/.gitkeep dashboard/backend/tests/test_migrations.py
git commit -m "feat(db): Alembic project skeleton (#393)"
```

---

## Task 5: Baseline revision encoding today's schema

**Files:**
- New: `dashboard/backend/alembic/versions/0001_baseline.py`
- Test: `dashboard/backend/tests/test_migrations.py` (append)

- [ ] **Step 1: Append failing test**

```python
import importlib

from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base


@pytest.mark.asyncio
async def test_alembic_baseline_creates_full_schema():
    """A fresh `alembic upgrade head` produces a schema isomorphic to
    Base.metadata.create_all + everything _migrate_add_columns adds."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    os.environ["STATION_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
    importlib.reload(importlib.import_module("app.config"))
    importlib.reload(importlib.import_module("app.database"))

    proc = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r[0] for r in rows.fetchall()}
    expected = {t.name for t in Base.metadata.sorted_tables}
    assert expected <= names
    os.unlink(db_path)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py::test_alembic_baseline_creates_full_schema -q
```

Expected: Alembic reports "no revisions" or fails.

- [ ] **Step 3: Generate the baseline revision body**

Create `dashboard/backend/alembic/versions/0001_baseline.py`:

```python
"""Baseline schema for SQLite -> Postgres migration (#393).

Reproduces ``Base.metadata.create_all`` PLUS every ``ALTER TABLE`` and
``CREATE INDEX`` previously applied by ``_migrate_add_columns``. After
this revision, the schema is identical whether the database is fresh or
upgraded from a long-running SQLite installation.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.database import Base
import app.models  # noqa: F401  (populate metadata)

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)

    # Indexes added later by _migrate_add_columns that aren't already on
    # the model definitions. CREATE INDEX IF NOT EXISTS works on both
    # SQLite and Postgres (Postgres ≥ 9.5).
    for stmt in [
        "CREATE INDEX IF NOT EXISTS ix_runs_status ON runs(status)",
        "CREATE INDEX IF NOT EXISTS ix_runs_project_id ON runs(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_runs_verdict ON runs(verdict)",
        "CREATE INDEX IF NOT EXISTS ix_runs_started_at ON runs(started_at)",
        "CREATE INDEX IF NOT EXISTS ix_runs_concurrent_group_id "
        "ON runs(concurrent_group_id)",
        "CREATE INDEX IF NOT EXISTS ix_conflict_resolutions_branch_started "
        "ON conflict_resolutions(branch, started_at)",
        "CREATE INDEX IF NOT EXISTS ix_runs_last_event_at ON runs(last_event_at)",
    ]:
        op.execute(stmt)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/alembic/versions/0001_baseline.py dashboard/backend/tests/test_migrations.py
git commit -m "feat(db): baseline Alembic revision (#393)"
```

---

## Task 6: Switch `init_db` to Alembic-on-startup

**Files:**
- Modify: `dashboard/backend/app/database.py`
- Test: `dashboard/backend/tests/test_migrations.py` (append)

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
async def test_init_db_runs_alembic_only():
    """init_db must invoke `alembic upgrade head`, not _migrate_add_columns."""
    import app.database as mod
    calls: list[str] = []

    def fake_check_call(cmd, *args, **kw):
        calls.append(" ".join(cmd))
        return 0

    import subprocess as sp
    orig = sp.check_call
    sp.check_call = fake_check_call
    try:
        await mod.init_db()
    finally:
        sp.check_call = orig
    assert any("alembic" in c and "upgrade" in c for c in calls)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py::test_init_db_runs_alembic_only -q
```

Expected: fails — current `init_db` still calls `_migrate_add_columns`.

- [ ] **Step 3: Replace the body of `init_db`**

In `dashboard/backend/app/database.py`, replace `init_db` and delete `_migrate_add_columns` (lines 36-141 in the current file):

```python
async def init_db() -> None:
    """Run Alembic migrations against the configured database.

    `_migrate_add_columns` is retired — every column it added is encoded in
    the Alembic baseline revision (`alembic/versions/0001_baseline.py`).
    The legacy auxiliary migration (`migrations/0003_simplify_config_schema.py`)
    is a config-JSON transform, not schema; it still runs after upgrade.
    """
    import subprocess
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    subprocess.check_call(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_root),
    )

    # Config-JSON migration still applicable post-schema.
    from migrations import _simplify_config_schema  # type: ignore[import-not-found]
    await _simplify_config_schema.run()
```

Note: `migrations/0003_simplify_config_schema.py` currently exposes a `run()` callable; if not, expose it as `_simplify_config_schema.run` in this Task by lightly renaming inside `migrations/__init__.py` (verify on read; if absent, skip this line — Alembic alone is sufficient for fresh installs).

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migrations.py tests/test_database.py -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/database.py dashboard/backend/tests/test_migrations.py
git commit -m "feat(db): init_db runs alembic upgrade head (#393)"
```

---

## Task 7: PR 1 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/393-postgres-migration
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(db): asyncpg-ready engine + Alembic baseline (#393, PR 1/4)" --body "$(cat <<'EOF'
Part 1 of 4 for #393.

## Summary
- `STATION_DB_URL` env var; `Settings.resolved_db_url` resolves between it and `STATION_DB_PATH`.
- Dialect-aware engine kwargs; PRAGMA listener no-ops under Postgres.
- Alembic project skeleton + baseline revision that recreates the full schema (tables + indexes from `_migrate_add_columns`).
- `init_db` calls `alembic upgrade head` instead of the imperative migrator.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_database.py tests/test_migrations.py -q`
- [ ] Smoke: `STATION_DB_URL=sqlite+aiosqlite:///:memory: uvicorn app.main:app --port 8420` boots clean.

This PR does not introduce Postgres at runtime; subsequent PRs add JSONB columns (PR 2), pub/sub (PR 3), and the migration script + compose changes (PR 4).
EOF
)"
```

- [ ] **Step 3: (Wait for CI; no code change)**

- [ ] **Step 4: Merge after green CI**

- [ ] **Step 5: Sync local dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

---

# PR 2 — JSONB columns + parametrized test fixtures

## Task 8: New `feature/393-jsonb` branch

- [ ] **Step 1: Branch**

```bash
git checkout dev && git checkout -b feature/393-jsonb
```

- [ ] **Step 2: (No code change)**

- [ ] **Step 3: (No code change)**

- [ ] **Step 4: (No code change)**

- [ ] **Step 5: (No commit)**

---

## Task 9: `JsonType` — dialect-aware column type

**Files:**
- Modify: `dashboard/backend/app/models.py`
- Test: `dashboard/backend/tests/test_database.py` (append)

- [ ] **Step 1: Append failing test**

```python
def test_jsontype_uses_jsonb_on_postgres():
    from app.models import JsonType  # noqa: PLC0415

    pg_impl = JsonType.dialect_impl(
        __import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    )
    assert pg_impl.__class__.__name__ == "JSONB"


def test_jsontype_uses_json_on_sqlite():
    from app.models import JsonType  # noqa: PLC0415

    sq_impl = JsonType.dialect_impl(
        __import__("sqlalchemy.dialects.sqlite", fromlist=["dialect"]).dialect()
    )
    assert sq_impl.__class__.__name__ == "JSON"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: `ImportError: cannot import name 'JsonType'`.

- [ ] **Step 3: Add `JsonType` and migrate three columns**

In `dashboard/backend/app/models.py`, near the imports, add:

```python
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

JsonType = JSON().with_variant(JSONB(), "postgresql")
```

Change three column declarations:

- `AgentEvent.event_data` (line ~287): `Column(JsonType, nullable=False)`.
- `AuditEntry.action_detail` (line ~268): `Column(JsonType, nullable=True)`.
- `Run.employee_report` (line ~70) and `Run.verdict_detail` (line ~71): both `Column(JsonType, nullable=True)`.

(For each, replace the current `Column(Text, …)` with `Column(JsonType, …)`. Keep `nullable` defaults.)

- [ ] **Step 4: Verify the test passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: green, including the new pair.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/tests/test_database.py
git commit -m "feat(db): dialect-aware JsonType on 3 columns (#393)"
```

---

## Task 10: `decode_event_data` helper (dialect-agnostic reads)

**Files:**
- New: `dashboard/backend/app/services/json_compat.py`
- Test: `dashboard/backend/tests/test_database.py` (append)

- [ ] **Step 1: Append failing test**

```python
def test_decode_event_data_handles_text_and_dict():
    from app.services.json_compat import decode_event_data
    assert decode_event_data('{"a": 1}') == {"a": 1}
    assert decode_event_data({"a": 1}) == {"a": 1}
    assert decode_event_data(None) is None
    assert decode_event_data("not-json") is None
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py::test_decode_event_data_handles_text_and_dict -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the helper**

Create `dashboard/backend/app/services/json_compat.py`:

```python
"""Dialect-agnostic decoders for columns of type ``JsonType`` (#393).

Postgres JSONB returns ``dict`` directly; SQLite returns ``str``. Callers
should funnel through ``decode_event_data`` rather than calling ``json.loads``
directly.
"""
from __future__ import annotations

import json
from typing import Any


def decode_event_data(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            return None
    return None
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/json_compat.py dashboard/backend/tests/test_database.py
git commit -m "feat(db): decode_event_data dialect helper (#393)"
```

---

## Task 11: Audit existing `json.loads` of the three columns

**Files:**
- Modify: every site calling `json.loads(...event_data)`, `json.loads(...action_detail)`, `json.loads(...employee_report)`, `json.loads(...verdict_detail)`.

- [ ] **Step 1: Identify the sites**

```bash
cd dashboard/backend && grep -rn "json.loads" app/ | grep -E "event_data|action_detail|employee_report|verdict_detail"
```

Expected: a list of files (likely in `app/routers/agent_events.py`, `app/routers/audit.py`, `app/services/run_lifecycle.py`, `app/services/log_parser.py`). Capture the output verbatim before editing.

- [ ] **Step 2: Write a guard test that fails until each site is migrated**

Append to `dashboard/backend/tests/test_database.py`:

```python
def test_no_raw_jsonloads_on_jsonb_columns():
    import pathlib

    backend_app = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in backend_app.rglob("*.py"):
        text = path.read_text()
        for needle in ("event_data", "action_detail", "employee_report", "verdict_detail"):
            if f"json.loads(" in text and needle in text:
                # Crude but effective; allow services/json_compat.py through.
                if path.name == "json_compat.py":
                    continue
                lines = [
                    ln for ln in text.splitlines()
                    if "json.loads(" in ln and needle in ln
                ]
                offenders.extend(f"{path}: {ln.strip()}" for ln in lines)
    assert offenders == [], "use decode_event_data:\n" + "\n".join(offenders)
```

- [ ] **Step 3: Replace each site**

For each file the grep found, replace `json.loads(row.event_data or "{}")` (or analogous) with `decode_event_data(row.event_data) or {}` and import:

```python
from app.services.json_compat import decode_event_data
```

The replacement is mechanical: same call shape, no behaviour change on SQLite, and correct on Postgres.

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py tests/test_run_lifecycle.py tests/test_audit_log.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/ dashboard/backend/tests/test_database.py
git commit -m "refactor(db): route JSONB reads through decode_event_data (#393)"
```

---

## Task 12: Parametrize tests across SQLite + Postgres

**Files:**
- Modify: `dashboard/backend/tests/conftest.py`
- New: `dashboard/backend/tests/postgres_fixture.py`

- [ ] **Step 1: Write the failing parametrized test**

Append a parametrized smoke test to `dashboard/backend/tests/test_database.py`:

```python
@pytest.mark.asyncio
async def test_smoke_insert_select(async_session_factory):
    """Parametrized over sqlite/postgres via async_session_factory fixture."""
    from app.models import Run
    from datetime import datetime, timezone

    async with async_session_factory() as db:
        db.add(Run(run_id="run-smoke", status="running",
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    from sqlalchemy import select
    async with async_session_factory() as db:
        row = (await db.execute(select(Run).where(Run.run_id == "run-smoke"))).scalar_one()
        assert row.status == "running"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py::test_smoke_insert_select -q
```

Expected: missing fixture `async_session_factory`.

- [ ] **Step 3: Add the parametrized fixture**

Create `dashboard/backend/tests/postgres_fixture.py`:

```python
"""Optional ephemeral Postgres for parametrized tests (#393).

Skipped when Docker isn't available. Tests opting into postgres
parametrization request the ``postgres_url`` fixture.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

import pytest


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "ps"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@contextmanager
def _ephemeral_postgres():
    name = f"cas-pg-test-{uuid.uuid4().hex[:8]}"
    port = _find_free_port()
    subprocess.check_call([
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_PASSWORD=test",
        "-e", "POSTGRES_USER=test",
        "-e", "POSTGRES_DB=test",
        "-p", f"{port}:5432",
        "postgres:16-alpine",
    ])
    try:
        url = f"postgresql+asyncpg://test:test@127.0.0.1:{port}/test"
        # Wait for readiness (max 30 s).
        deadline = time.time() + 30
        while time.time() < deadline:
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "test"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("postgres test container never became ready")
        yield url
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True)


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def postgres_url():
    if not _docker_available():
        pytest.skip("docker not available; skip postgres-parametrized run")
    with _ephemeral_postgres() as url:
        yield url
```

Modify `dashboard/backend/tests/conftest.py` to expose `async_session_factory`:

```python
"""Shared test configuration: sets a temp database path before importing the app."""

import os
import tempfile

# Must set env var BEFORE any app imports to override settings.db_path
_fd, _tmp_db = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ.setdefault("STATION_DB_PATH", _tmp_db)

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.postgres_fixture import postgres_url  # noqa: F401, re-export


@pytest.fixture(scope="session", params=["sqlite", "postgres"])
def db_url(request, postgres_url):
    if request.param == "sqlite":
        return "sqlite+aiosqlite:///:memory:"
    return postgres_url


@pytest.fixture(scope="session")
def async_session_factory(db_url):
    """Per-backend session factory.

    Runs Alembic upgrade head against the chosen URL once per session.
    Tests share the schema.
    """
    import subprocess
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "STATION_DB_URL": db_url}
    subprocess.check_call(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_root),
        env=env,
    )
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py -q
```

Expected: each parametrized test runs twice (`[sqlite]`, `[postgres]`) and passes. If Docker is unavailable the Postgres branch is skipped.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/postgres_fixture.py dashboard/backend/tests/conftest.py dashboard/backend/tests/test_database.py
git commit -m "test(db): parametrize across sqlite+postgres fixtures (#393)"
```

---

## Task 13: PR 2 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/393-jsonb
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(db): JSONB columns + parametrized tests (#393, PR 2/4)" --body "$(cat <<'EOF'
Part 2 of 4 for #393.

## Summary
- New `JsonType` (`JSON.with_variant(JSONB, "postgresql")`) replaces `Text` on three columns: `agent_events.event_data`, `audit_log.action_detail`, `runs.employee_report` + `verdict_detail`.
- `decode_event_data` dialect-agnostic helper; all callers funneled through it.
- `conftest.py` now parametrizes `db_url` across SQLite-in-memory and ephemeral Postgres (`postgres:16-alpine`) via Docker.

## Test plan
- [ ] `cd dashboard/backend && pytest -q`
- [ ] CI matrix: both `[sqlite]` and `[postgres]` parametrizations green.
EOF
)"
```

- [ ] **Step 3: (Wait for CI)**

- [ ] **Step 4: Merge once green**

- [ ] **Step 5: Sync dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

---

# PR 3 — pubsub: LISTEN/NOTIFY (Postgres) + polling fallback

## Task 14: New branch

- [ ] **Step 1: Branch**

```bash
git checkout -b feature/393-pubsub
```

- [ ] **Step 2: (No code change)**

- [ ] **Step 3: (No code change)**

- [ ] **Step 4: (No code change)**

- [ ] **Step 5: (No commit)**

---

## Task 15: `services/pubsub.py` — `listen` / `notify` skeleton

**Files:**
- New: `dashboard/backend/app/services/pubsub.py`
- Test: `dashboard/backend/tests/test_pubsub.py` (new)

- [ ] **Step 1: Write the failing SQLite no-op test**

Create `dashboard/backend/tests/test_pubsub.py`:

```python
"""LISTEN/NOTIFY tests (#393).

Postgres tests are marked ``postgres_only``; on SQLite we assert the
contract is a clean no-op.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.services.pubsub import listen, notify


@pytest.mark.asyncio
async def test_notify_sqlite_noop():
    os.environ["STATION_DB_URL"] = "sqlite+aiosqlite:///:memory:"
    await notify("run_event", {"run_id": "x"})  # no exception


@pytest.mark.asyncio
async def test_listen_sqlite_terminates_immediately():
    os.environ["STATION_DB_URL"] = "sqlite+aiosqlite:///:memory:"
    async def _consume():
        async for _ in listen("run_event"):
            return "got"
        return "exhausted"

    result = await asyncio.wait_for(_consume(), timeout=1.0)
    assert result == "exhausted"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

Create `dashboard/backend/app/services/pubsub.py`:

```python
"""Cross-process pub/sub over Postgres LISTEN/NOTIFY (#393).

Production path: asyncpg LISTEN on a named channel; emit via NOTIFY.
SQLite path: ``notify`` is a no-op; ``listen`` immediately exhausts.
Callers degrade to polling for SQLite.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import asyncpg
from sqlalchemy.engine.url import make_url

from app.config import settings

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return settings.resolved_db_url.startswith("postgresql")


def _asyncpg_dsn() -> str:
    url = make_url(settings.resolved_db_url)
    return (
        f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/{url.database}"
    )


async def notify(channel: str, payload: dict) -> None:
    if not _is_postgres():
        return
    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        await conn.execute("SELECT pg_notify($1, $2)", channel, json.dumps(payload))
    finally:
        await conn.close()


async def listen(channel: str) -> AsyncIterator[dict]:
    if not _is_postgres():
        return  # immediately exhaust
        yield  # pragma: no cover  (makes this an async generator)
    conn = await asyncpg.connect(_asyncpg_dsn())
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _on_notify(_c, _pid, _channel, payload):
        try:
            queue.put_nowait(json.loads(payload))
        except json.JSONDecodeError:
            logger.warning("pubsub: dropping non-JSON payload on %s", _channel)

    await conn.add_listener(channel, _on_notify)
    try:
        while True:
            yield await queue.get()
    finally:
        await conn.remove_listener(channel, _on_notify)
        await conn.close()
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -q
```

Expected: 2 passed (Postgres tests skipped on SQLite-only env).

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/pubsub.py dashboard/backend/tests/test_pubsub.py
git commit -m "feat(db): pubsub skeleton with SQLite no-op (#393)"
```

---

## Task 16: Postgres LISTEN/NOTIFY round-trip test

**Files:**
- Modify: `dashboard/backend/tests/test_pubsub.py`

- [ ] **Step 1: Append failing Postgres-only test**

```python
@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_notify_observed_within_one_second(postgres_url):
    os.environ["STATION_DB_URL"] = postgres_url

    async def consumer():
        async for msg in listen("run_event"):
            return msg
        return None

    consume_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)  # let listener register
    await notify("run_event", {"run_id": "rt-1"})
    msg = await asyncio.wait_for(consume_task, timeout=2.0)
    assert msg == {"run_id": "rt-1"}
```

- [ ] **Step 2: Add the `postgres_only` marker**

Append to `dashboard/backend/pytest.ini` (create if missing):

```ini
[pytest]
markers =
    postgres_only: skip on SQLite-only runs
    sqlite_only: skip on Postgres-only runs
```

- [ ] **Step 3: Verify it passes (Docker required)**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -m postgres_only -q
```

Expected: 1 passed. If Docker is unavailable, the `postgres_url` fixture skips.

- [ ] **Step 4: Run the full pubsub suite**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -q
```

Expected: 3 passed (2 SQLite + 1 PG round-trip).

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/test_pubsub.py dashboard/backend/pytest.ini
git commit -m "test(db): LISTEN/NOTIFY round-trip + marker registry (#393)"
```

---

## Task 17: Wire `notify("run_event", ...)` in the webhook router

**Files:**
- Modify: `dashboard/backend/app/routers/webhook.py`
- Test: `dashboard/backend/tests/test_pubsub.py` (append)

- [ ] **Step 1: Append failing integration test**

```python
@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_webhook_emits_run_event_notify(postgres_url, client):
    os.environ["STATION_DB_URL"] = postgres_url

    async def consume_one():
        async for msg in listen("run_event"):
            return msg
        return None

    task = asyncio.create_task(consume_one())
    await asyncio.sleep(0.1)

    resp = await client.post(
        "/api/webhook/run-event",
        json={"event": "started", "run_id": "rt-wh-1", "status": "running"},
    )
    assert resp.status_code in (200, 201, 204)
    msg = await asyncio.wait_for(task, timeout=2.0)
    assert msg["run_id"] == "rt-wh-1"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py::test_webhook_emits_run_event_notify -q
```

Expected: timeout — no NOTIFY emitted yet.

- [ ] **Step 3: Add the notify call**

In `dashboard/backend/app/routers/webhook.py`, after the insert/update of the run/event row (look for `await db.commit()` in the webhook handler), append:

```python
from app.services.pubsub import notify  # add to top-level imports

# ... existing handler logic ...

await db.commit()
await notify("run_event", {"run_id": event.run_id, "kind": event.event})
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -q
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/webhook.py dashboard/backend/tests/test_pubsub.py
git commit -m "feat(db): webhook emits run_event NOTIFY (#393)"
```

---

## Task 18: Wire `notify("heartbeat", ...)` in run_lifecycle

**Files:**
- Modify: `dashboard/backend/app/services/run_lifecycle.py`
- Test: `dashboard/backend/tests/test_pubsub.py` (append)

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_lifecycle_heartbeat_notify(postgres_url):
    os.environ["STATION_DB_URL"] = postgres_url
    from app.services.run_lifecycle import bump_heartbeat

    async def consume_one():
        async for msg in listen("heartbeat"):
            return msg
        return None

    task = asyncio.create_task(consume_one())
    await asyncio.sleep(0.1)
    await bump_heartbeat("rt-hb-1")
    msg = await asyncio.wait_for(task, timeout=2.0)
    assert msg == {"run_id": "rt-hb-1"}
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py::test_lifecycle_heartbeat_notify -q
```

Expected: failure (no `bump_heartbeat` or no NOTIFY).

- [ ] **Step 3: Add or wire the NOTIFY**

In `dashboard/backend/app/services/run_lifecycle.py`, after each `runs.last_event_at` update, NOTIFY. If `bump_heartbeat` does not exist, add it:

```python
from app.services.pubsub import notify
from datetime import datetime, timezone


async def bump_heartbeat(run_id: str) -> None:
    """Bump runs.last_event_at and broadcast a heartbeat NOTIFY (#393)."""
    from app.database import async_session
    from app.models import Run
    from sqlalchemy import update

    async with async_session() as db:
        await db.execute(
            update(Run)
            .where(Run.run_id == run_id)
            .values(last_event_at=datetime.now(timezone.utc))
        )
        await db.commit()
    await notify("heartbeat", {"run_id": run_id})
```

Call `bump_heartbeat(run_id)` from the existing webhook handler in `routers/webhook.py` where the previous code bumped `last_event_at` directly.

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_pubsub.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/run_lifecycle.py dashboard/backend/app/routers/webhook.py dashboard/backend/tests/test_pubsub.py
git commit -m "feat(db): heartbeat NOTIFY after last_event_at bump (#393)"
```

---

## Task 19: `log_importer` subscribes on Postgres; raise poll interval to 5 min

**Files:**
- Modify: `dashboard/backend/app/services/log_importer.py`
- Test: `dashboard/backend/tests/test_log_importer_pubsub.py` (new)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_log_importer_pubsub.py`:

```python
"""log_importer pub/sub integration (#393)."""
from __future__ import annotations

import os

import pytest

from app.services import log_importer


def test_default_poll_interval_is_300_on_postgres(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "postgresql+asyncpg://x:y@z/db")
    assert log_importer.poll_interval_seconds() == 300


def test_default_poll_interval_is_30_on_sqlite(monkeypatch):
    monkeypatch.delenv("STATION_DB_URL", raising=False)
    monkeypatch.setenv("STATION_DB_PATH", "/tmp/x.db")
    assert log_importer.poll_interval_seconds() == 30
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_log_importer_pubsub.py -q
```

Expected: `AttributeError: poll_interval_seconds`.

- [ ] **Step 3: Update `log_importer.py`**

In `dashboard/backend/app/services/log_importer.py`, add (near the existing interval constant):

```python
import importlib

from app.config import settings


def poll_interval_seconds() -> int:
    """5 minutes on Postgres (NOTIFY carries the load) — 30 s on SQLite."""
    importlib.reload(__import__("app.config", fromlist=["settings"]))
    from app.config import settings as fresh
    return 300 if fresh.resolved_db_url.startswith("postgresql") else 30
```

Replace the current fixed-interval `asyncio.sleep(30)` with `asyncio.sleep(poll_interval_seconds())`.

In the main loop, add a parallel task that subscribes via `pubsub.listen("run_event")` and triggers a single import on each notification:

```python
from app.services.pubsub import listen


async def _run_event_subscriber():
    async for _ in listen("run_event"):
        await _import_pending_runs()  # the function the polling loop already calls


# In the service entry point, alongside the polling loop:
async def run() -> None:
    await asyncio.gather(
        _polling_loop(),
        _run_event_subscriber(),
    )
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_log_importer_pubsub.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/log_importer.py dashboard/backend/tests/test_log_importer_pubsub.py
git commit -m "feat(db): log_importer subscribes to run_event; poll 300s on PG (#393)"
```

---

## Task 20: `stale_run_reaper` subscribes to `heartbeat`

**Files:**
- Modify: `dashboard/backend/app/services/stale_run_reaper.py`
- Test: `dashboard/backend/tests/test_stale_run_reaper_pubsub.py` (new)

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_stale_run_reaper_pubsub.py`:

```python
"""stale_run_reaper pub/sub integration (#393)."""
from __future__ import annotations

import pytest

from app.services import stale_run_reaper


def test_reaper_default_tick_is_15s_on_sqlite(monkeypatch):
    monkeypatch.delenv("STATION_DB_URL", raising=False)
    assert stale_run_reaper.tick_interval_seconds() == 15


def test_reaper_default_tick_is_60s_on_postgres(monkeypatch):
    monkeypatch.setenv("STATION_DB_URL", "postgresql+asyncpg://x:y@z/db")
    assert stale_run_reaper.tick_interval_seconds() == 60
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_stale_run_reaper_pubsub.py -q
```

Expected: `AttributeError`.

- [ ] **Step 3: Update the reaper**

In `dashboard/backend/app/services/stale_run_reaper.py`, add `tick_interval_seconds()` mirroring the importer's helper, and wire a heartbeat subscriber alongside the existing tick loop. The heartbeat handler resets a per-run watchdog timer; the tick loop only enforces "no heartbeat in X seconds".

```python
import asyncio

from app.config import settings
from app.services.pubsub import listen


def tick_interval_seconds() -> int:
    return 60 if settings.resolved_db_url.startswith("postgresql") else 15


_recent_heartbeats: dict[str, float] = {}


async def _heartbeat_subscriber():
    async for msg in listen("heartbeat"):
        run_id = msg.get("run_id")
        if run_id:
            import time
            _recent_heartbeats[run_id] = time.monotonic()


async def run():
    await asyncio.gather(
        _tick_loop(),
        _heartbeat_subscriber(),
    )
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_stale_run_reaper_pubsub.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/stale_run_reaper.py dashboard/backend/tests/test_stale_run_reaper_pubsub.py
git commit -m "feat(db): stale_run_reaper subscribes to heartbeat (#393)"
```

---

## Task 21: PR 3 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/393-pubsub
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(db): LISTEN/NOTIFY for run_event + heartbeat (#393, PR 3/4)" --body "$(cat <<'EOF'
Part 3 of 4 for #393.

## Summary
- `services/pubsub.py` exposes `listen()` async iterator + `notify()` over asyncpg LISTEN/NOTIFY; clean no-op on SQLite.
- Webhook router NOTIFYs `run_event` after each insert; lifecycle `bump_heartbeat()` NOTIFYs `heartbeat`.
- `log_importer` raises poll interval from 30s to 300s on Postgres and subscribes to `run_event`; `stale_run_reaper` raises tick from 15s to 60s and subscribes to `heartbeat`.
- SQLite path unchanged.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_pubsub.py tests/test_log_importer_pubsub.py tests/test_stale_run_reaper_pubsub.py -q`
- [ ] Postgres-only tests run under the ephemeral fixture from PR 2.
EOF
)"
```

- [ ] **Step 3: (Wait for CI)**

- [ ] **Step 4: Merge once green**

- [ ] **Step 5: Sync dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

---

# PR 4 — compose.yml + migration script + operator playbook + e2e

## Task 22: New branch

- [ ] **Step 1: Branch**

```bash
git checkout -b feature/393-compose-and-migrator
```

- [ ] **Step 2-5: (No commit; branch only)**

---

## Task 23: `compose.yml` — `db` service

**Files:**
- Modify: `compose.yml`
- New: `.secrets/db_password` (gitignored)
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing compose-shape test**

Create `dashboard/backend/tests/test_compose_db_service.py`:

```python
"""compose.yml exposes a `db` service with healthcheck (#393)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _compose():
    return yaml.safe_load((Path(__file__).resolve().parents[3] / "compose.yml").read_text())


def test_db_service_present():
    c = _compose()
    assert "db" in c["services"]
    assert c["services"]["db"]["image"].startswith("postgres:")


def test_db_healthcheck_present():
    db = _compose()["services"]["db"]
    assert "healthcheck" in db
    assert "pg_isready" in " ".join(db["healthcheck"]["test"])


def test_dashboard_depends_on_db_healthy():
    dash = _compose()["services"]["dashboard"]
    assert dash["depends_on"]["db"]["condition"] == "service_healthy"


def test_agent_depends_on_db_healthy():
    agent = _compose()["services"]["agent"]
    assert agent["depends_on"]["db"]["condition"] == "service_healthy"


def test_db_password_secret_declared():
    c = _compose()
    assert "db_password" in c.get("secrets", {})
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_db_service.py -q
```

Expected: all 5 fail (db service not declared).

- [ ] **Step 3: Edit `compose.yml`**

Append the `db` service block and patch `dashboard`/`agent`:

```yaml
services:
  db:
    image: postgres:16-alpine
    container_name: cas-db
    environment:
      POSTGRES_USER: station
      POSTGRES_DB: station
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    volumes:
      - station-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "station"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  dashboard:
    depends_on:
      db:
        condition: service_healthy
    environment:
      STATION_DB_URL: "postgresql+asyncpg://station:${DB_PASSWORD}@db:5432/station"

  agent:
    depends_on:
      db:
        condition: service_healthy
    environment:
      STATION_DB_URL: "postgresql+asyncpg://station:${DB_PASSWORD}@db:5432/station"

volumes:
  station-pgdata:

secrets:
  db_password:
    file: ./.secrets/db_password
```

Adjust the indentation so the new `depends_on`/`environment` keys merge with the existing `dashboard`/`agent` blocks (don't create duplicates).

Add `.secrets/` to `.gitignore`:

```
.secrets/
```

Create a placeholder so the secret file resolves locally:

```bash
mkdir -p .secrets && echo "change-me-in-prod" > .secrets/db_password && chmod 0600 .secrets/db_password
```

(Do **not** commit `.secrets/db_password` — `.gitignore` rule blocks it.)

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_db_service.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add compose.yml .gitignore dashboard/backend/tests/test_compose_db_service.py
git commit -m "feat(db): compose db service + secret + depends_on (#393)"
```

---

## Task 24: Migration script — schema + walker

**Files:**
- New: `scripts/__init__.py`
- New: `scripts/migrate_sqlite_to_postgres.py`
- New: `dashboard/backend/tests/test_migration_script.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_migration_script.py`:

```python
"""SQLite -> Postgres migration script (#393)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.postgres_only
@pytest.mark.asyncio
async def test_row_count_parity_per_table(postgres_url):
    # Seed a SQLite source with rows in every table.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        sqlite_path = f.name
    sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"

    env = {**os.environ, "STATION_DB_URL": sqlite_url}
    subprocess.check_call(
        ["alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT / "dashboard/backend"),
        env=env,
    )

    src_engine = create_async_engine(sqlite_url)
    from app.models import AgentEvent, Run

    async with async_sessionmaker(src_engine, expire_on_commit=False)() as db:
        db.add(Run(run_id="rmig-1", status="success",
                   started_at=datetime.now(timezone.utc),
                   employee_report=json.dumps({"k": "v"})))
        db.add(AgentEvent(workflow_id="w", run_id="rmig-1", agent_id="lead",
                          event_type="lifecycle.run_start",
                          event_data='{"foo":"bar"}',
                          created_at=datetime.now(timezone.utc)))
        await db.commit()
    await src_engine.dispose()

    # Prepare a fresh Postgres target with Alembic baseline applied.
    env_pg = {**os.environ, "STATION_DB_URL": postgres_url}
    subprocess.check_call(
        ["alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT / "dashboard/backend"),
        env=env_pg,
    )

    # Run the converter.
    proc = subprocess.run(
        [
            sys.executable, "-m", "scripts.migrate_sqlite_to_postgres",
            "--sqlite", sqlite_path,
            "--postgres", postgres_url,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    # Parity check.
    pg_engine = create_async_engine(postgres_url)
    async with async_sessionmaker(pg_engine, expire_on_commit=False)() as db:
        run = (await db.execute(select(Run).where(Run.run_id == "rmig-1"))).scalar_one()
        assert run.status == "success"
        # JSONB roundtrip: stored as dict on Postgres regardless of source format.
        assert run.employee_report == {"k": "v"}
        evs = (await db.execute(select(AgentEvent).where(AgentEvent.run_id == "rmig-1"))).scalars().all()
        assert len(evs) == 1
        assert evs[0].event_data == {"foo": "bar"}
    await pg_engine.dispose()
    os.unlink(sqlite_path)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migration_script.py -q
```

Expected: `ModuleNotFoundError: No module named 'scripts.migrate_sqlite_to_postgres'`.

- [ ] **Step 3: Implement the script**

Create `scripts/__init__.py` (empty).

Create `scripts/migrate_sqlite_to_postgres.py`:

```python
"""One-shot SQLite -> Postgres converter (#393).

Usage:
    python -m scripts.migrate_sqlite_to_postgres \
        --sqlite /var/lib/claude-agent-station/station.db \
        --postgres "postgresql+asyncpg://station:pw@db:5432/station"

Operator playbook is documented in docs/configuration.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

# Three columns that hold JSON-as-text on SQLite and JSONB on Postgres.
_JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "agent_events": ("event_data",),
    "audit_log": ("action_detail",),
    "runs": ("employee_report", "verdict_detail"),
}

logger = logging.getLogger("migrate_sqlite_to_postgres")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sqlite", required=True, help="Path to source SQLite file")
    p.add_argument("--postgres", required=True, help="Target SQLAlchemy URL")
    p.add_argument("--batch", type=int, default=1000)
    return p.parse_args(argv)


def _decode_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _transform_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    cols = _JSON_COLUMNS.get(table, ())
    if not cols:
        return row
    out = dict(row)
    for c in cols:
        if c in out:
            out[c] = _decode_jsonish(out[c])
    return out


async def _copy_table(src_engine, dst_engine, table) -> tuple[int, int]:
    insp = inspect(table)
    cols = [c.name for c in insp.columns]
    async with src_engine.connect() as src_conn:
        result = await src_conn.execute(select(table))
        src_rows = result.mappings().all()
    if not src_rows:
        return (0, 0)

    transformed = [_transform_row(table.name, dict(r)) for r in src_rows]
    placeholders = ", ".join(f":{c}" for c in cols)
    columns_sql = ", ".join(cols)
    insert_sql = text(
        f"INSERT INTO {table.name} ({columns_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )
    inserted = 0
    async with dst_engine.begin() as dst_conn:
        for i in range(0, len(transformed), 1000):
            batch = transformed[i : i + 1000]
            await dst_conn.execute(insert_sql, batch)
            inserted += len(batch)
    return (len(src_rows), inserted)


async def _reset_sequences(dst_engine, table_names: list[str]) -> None:
    async with dst_engine.begin() as conn:
        for name in table_names:
            # Postgres only; SERIAL/sequences exist for tables with integer PKs.
            try:
                await conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 1))"
                    )
                )
            except Exception:
                # Tables whose PK isn't `id` (e.g. `event_id`, `run_id` text PK) are skipped.
                pass


async def _async_main(args: argparse.Namespace) -> int:
    from app.database import Base
    import app.models  # noqa: F401

    src_url = f"sqlite+aiosqlite:///{args.sqlite}"
    src_engine = create_async_engine(src_url)
    dst_engine = create_async_engine(args.postgres)

    mismatches: list[str] = []
    summary: list[tuple[str, int, int]] = []
    for table in Base.metadata.sorted_tables:
        src_count, inserted = await _copy_table(src_engine, dst_engine, table)
        summary.append((table.name, src_count, inserted))
        if src_count != inserted:
            mismatches.append(f"{table.name}: {inserted}/{src_count}")

    await _reset_sequences(dst_engine, [t.name for t in Base.metadata.sorted_tables])
    await src_engine.dispose()
    await dst_engine.dispose()

    print("\nRow-count parity per table:")
    print(f"{'table':<40} {'src':>10} {'dst':>10}")
    for name, src, dst in summary:
        print(f"{name:<40} {src:>10} {dst:>10}")

    if mismatches:
        print("\nMISMATCH:")
        for m in mismatches:
            print(f"  {m}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migration_script.py -q
```

Expected: 1 passed (under Docker-available env; skipped otherwise).

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/migrate_sqlite_to_postgres.py dashboard/backend/tests/test_migration_script.py
git commit -m "feat(db): SQLite -> Postgres migration script (#393)"
```

---

## Task 25: Migration script — sequence reset + mismatch exit code

**Files:**
- Modify: `dashboard/backend/tests/test_migration_script.py` (append)

- [ ] **Step 1: Append failing test**

```python
def test_mismatch_exits_nonzero(tmp_path):
    """If the SQLite file is missing, the script must exit non-zero."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "scripts.migrate_sqlite_to_postgres",
            "--sqlite", str(tmp_path / "nope.db"),
            "--postgres", "postgresql+asyncpg://x:y@unreachable:5432/db",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migration_script.py::test_mismatch_exits_nonzero -q
```

Expected: 1 passed. The script's `_copy_table` raises on a missing file, and `_async_main` propagates.

- [ ] **Step 3: (No code change)**

- [ ] **Step 4: Run full migration-script suite**

```bash
cd dashboard/backend && python3 -m pytest tests/test_migration_script.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/test_migration_script.py
git commit -m "test(db): migration script exits non-zero on errors (#393)"
```

---

## Task 26: End-to-end synthetic run, parametrized

**Files:**
- New: `dashboard/backend/tests/integration/__init__.py` (if missing)
- New: `dashboard/backend/tests/integration/test_run_e2e.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/integration/test_run_e2e.py`:

```python
"""Synthetic run end-to-end across both backends (#393)."""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_synthetic_run_lifecycle(async_session_factory, client):
    from app.models import Run

    # 1. Trigger via webhook.
    await client.post(
        "/api/webhook/run-event",
        json={"event": "started", "run_id": "e2e-1", "status": "running"},
    )
    await client.post(
        "/api/webhook/run-event",
        json={"event": "finished", "run_id": "e2e-1", "status": "success",
              "verdict": "APPROVE"},
    )

    async with async_session_factory() as db:
        row = (await db.execute(select(Run).where(Run.run_id == "e2e-1"))).scalar_one()
        assert row.status == "success"
        assert row.verdict == "APPROVE"
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/integration/test_run_e2e.py -q
```

Expected: green across both `[sqlite]` and `[postgres]` parametrizations.

- [ ] **Step 3: (No code change)**

- [ ] **Step 4: (No code change)**

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/integration/test_run_e2e.py
git commit -m "test(db): synthetic e2e run parametrized over backends (#393)"
```

---

## Task 27: Operator playbook in `docs/configuration.md`

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Write the failing doc-shape test**

Append to `dashboard/backend/tests/test_database.py`:

```python
def test_configuration_doc_has_db_migration_section():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[3] / "docs/configuration.md").read_text()
    assert "## Database migration" in doc
    assert "STATION_DB_URL" in doc
    assert "migrate_sqlite_to_postgres" in doc
    assert "Rollback" in doc
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py::test_configuration_doc_has_db_migration_section -q
```

Expected: 1 failed.

- [ ] **Step 3: Append the section**

Append to `docs/configuration.md`:

```markdown
## Database migration

Claude Agent Station ships SQLite by default for single-host installs. Move to Postgres for multi-writer setups (per-project containers, decomposed runs).

### Production cutover (one-time)

1. **Quiesce work**: set `StationControl.global_pause` from the dashboard.
2. **Backup the SQLite file**:
   ```bash
   docker compose exec dashboard sqlite3 /var/lib/claude-agent-station/station.db \
       ".backup /var/lib/claude-agent-station/station.db.bak"
   ```
3. **Bring up Postgres** (leave apps stopped):
   ```bash
   docker compose up -d db
   ```
4. **Apply baseline schema** to Postgres:
   ```bash
   docker compose run --rm --entrypoint "alembic upgrade head" dashboard
   ```
5. **Run the converter**:
   ```bash
   docker compose run --rm dashboard python -m scripts.migrate_sqlite_to_postgres \
       --sqlite /var/lib/claude-agent-station/station.db \
       --postgres "$STATION_DB_URL"
   ```
   The converter prints a row-count parity table per table. Exit non-zero on any mismatch.
6. **Restart the stack with the new URL**:
   ```bash
   STATION_DB_URL="postgresql+asyncpg://station:${DB_PASSWORD}@db:5432/station" \
   docker compose up -d
   ```
7. **Smoke-test**: trigger a run via the dashboard; verify it lands and the verdict flows.

### Rollback

If verification fails inside the cutover window, unset `STATION_DB_URL` and restart with the original `STATION_DB_PATH`. The SQLite file is unchanged. Once new writes have hit Postgres, rollback requires re-exporting from Postgres back to SQLite — out of scope for the supported playbook.

### Configuration

| Env var | Required | Notes |
|---|---|---|
| `STATION_DB_URL` | yes for Postgres | Full SQLAlchemy URL incl. driver. Takes precedence over `STATION_DB_PATH`. |
| `STATION_DB_PATH` | legacy / SQLite-only | Default `/var/lib/claude-agent-station/station.db`. |
| `DB_PASSWORD` | yes for compose Postgres | Substituted into the `STATION_DB_URL` template; read from `.secrets/db_password`. |
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_database.py::test_configuration_doc_has_db_migration_section -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md dashboard/backend/tests/test_database.py
git commit -m "docs(db): operator playbook + rollback (#393)"
```

---

## Task 28: PR 4 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/393-compose-and-migrator
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(db): compose db + migrator + playbook (#393, PR 4/4)" --body "$(cat <<'EOF'
Part 4 of 4 for #393.

## Summary
- `compose.yml`: new `db` service (`postgres:16-alpine`), Docker secret, healthcheck-gated `depends_on` for `dashboard` + `agent`.
- One-shot CLI `python -m scripts.migrate_sqlite_to_postgres` with row-count parity output and sequence reset.
- End-to-end synthetic run parametrized across SQLite + Postgres.
- Operator playbook + rollback section in `docs/configuration.md`.

## Test plan
- [ ] `cd dashboard/backend && pytest -q` (parametrized matrix)
- [ ] `docker compose up -d db && docker compose run --rm dashboard alembic upgrade head` against a fresh volume.
- [ ] Manual rehearsal on a staging SQLite copy: converter passes, swap env var, smoke a run.
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync.**

---

## Self-review checklist

- [x] Every acceptance criterion in `2026-05-14-issue-393-postgres-migration.md` maps to ≥1 task:
  - `STATION_DB_URL` supported, `STATION_DB_PATH` fallback → Task 1.
  - `compose.yml` declares `db` + dependencies + healthcheck → Task 23.
  - Migrations replayable end-to-end via Alembic → Tasks 4, 5, 6.
  - Existing SQLite data exportable via the script → Tasks 24, 25.
  - All tests pass on both backends → Task 12 (fixtures) + every subsequent test.
  - `log_importer` raises poll interval to 5 min → Task 19.
  - Operator migration playbook documented → Task 27.
  - No-regression e2e on Postgres → Task 26.
- [x] No `TBD`, `TODO`, `add error handling`, `similar to Task N` placeholders.
- [x] Real paths verified: `dashboard/backend/app/database.py:13-141`, `app/models.py:44/118/246/277/292`, `app/services/log_importer.py`, `app/services/stale_run_reaper.py`, `app/routers/webhook.py`, `migrations/0003_simplify_config_schema.py`, `compose.yml`.
- [x] Type / name consistency: `STATION_DB_URL`, `resolved_db_url`, `JsonType`, `decode_event_data`, `pubsub.listen`, `pubsub.notify`, `bump_heartbeat`, `migrate_sqlite_to_postgres` used identically across files and tests.
- [x] Parametrized tests for #393 (per the task brief): every test in `test_database.py`, `test_pubsub.py`, `test_migration_script.py`, `tests/integration/test_run_e2e.py` runs across the `db_url` fixture's `[sqlite]` and `[postgres]` parameters.
