# SQLite → Postgres Migration — Design

**Status**: design
**Date**: 2026-05-14
**Author**: tier-2-architect
**Issue**: [#393](https://github.com/kenhaesler/claude-agent-station/issues/393) — *Tier 2 / Issue D* of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)

## Context

All persistence in Claude Agent Station today goes through a single SQLite file at `/var/lib/claude-agent-station/station.db`. Connection setup lives at `dashboard/backend/app/database.py:13`:

```python
DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"
engine = create_async_engine(DATABASE_URL, echo=False)
```

The connect listener forces WAL mode and `PRAGMA foreign_keys=ON`. Migrations are imperative — a hand-written `_migrate_add_columns` function walks a list of `(table, column, sql)` tuples on every startup, plus one auxiliary script at `dashboard/backend/migrations/0003_simplify_config_schema.py`. There are ~30 tables defined in `dashboard/backend/app/models.py`; the high-volume ones are `runs`, `audit_log`, `agent_events`, `coordinator_tasks`, `task_outcomes`.

The schema is fine; the dialect is the bottleneck. Single-writer was acceptable while the `cas-agent` container hosted all four agents in one process tree. Tier 2 / Issue A ([#386](https://github.com/kenhaesler/claude-agent-station/issues/386), see [[2026-05-14-issue-386-per-project-containers]]) breaks that invariant: per-project ephemeral containers spawn N concurrent agent processes, each writing to `agent_events` and `audit_log`. Under SQLite's whole-DB write lock, peak event rates (~50 events/sec measured during `run-20260513T151408Z`) start producing `database is locked` errors and webhook handler stalls. The `task_queue` issue-claim race relies on SQLite's atomic-write semantics; multi-writer needs row-level locking. And two services — `log_importer` (30 s rescan) and `stale_run_reaper` (15 s tick) — poll because SQLite has no pub/sub primitive. Postgres' LISTEN/NOTIFY would replace both at near-zero cost.

This spec defines the swap to Postgres as a backend-only change: same ORM (SQLAlchemy), same models, same migrations API surface. SQLite stays supported as the local-dev / test fallback so the migration doesn't force a Postgres dependency on contributors. Per-test fixtures keep using SQLite in-memory; integration tests opt into a throwaway Postgres container.

## Goals

- Add Postgres as the production database driver (asyncpg via SQLAlchemy async).
- Keep SQLite working unchanged as a local-dev / test fallback.
- Move the imperative `_migrate_add_columns` walker to Alembic revisions that replay against either backend.
- Replace `log_importer` and `stale_run_reaper` polling with LISTEN/NOTIFY on Postgres; keep the polling fallback for SQLite.
- Document a one-shot operator migration playbook (backup → dump → restore → swap env var → verify) with a documented rollback.

## Non-goals

- Sharding, read replicas, multi-region — volume is nowhere near that.
- Switching ORMs. SQLAlchemy stays.
- Renaming tables or columns. Pure dialect swap.
- Replacing pub/sub with Redis / RabbitMQ / Kafka — Postgres LISTEN/NOTIFY is enough at this volume; see the issue's "Why not RabbitMQ" callout.
- Encrypting columns at rest. Out of scope; existing file-permissions story is unchanged.

## Approach

### 1. Configuration: `STATION_DB_URL`

`dashboard/backend/app/config.py` gains a new field:

```python
class Settings(BaseSettings):
    db_path: str = "/var/lib/claude-agent-station/station.db"   # legacy, SQLite-only
    db_url: str | None = None                                   # preferred, full URL
    ...

    @property
    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite+aiosqlite:///{self.db_path}"
```

`dashboard/backend/app/database.py` switches:

```python
DATABASE_URL = settings.resolved_db_url
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=20 if DATABASE_URL.startswith("postgresql") else 5,
    max_overflow=10 if DATABASE_URL.startswith("postgresql") else 0,
)
```

The SQLite-only `PRAGMA` listener is wrapped in a dialect check so it no-ops under Postgres:

```python
@event.listens_for(engine.sync_engine, "connect")
def _on_connect(dbapi_conn, _):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

The `STATION_DB_PATH` env var keeps working as the SQLite path; `STATION_DB_URL` overrides it. Default for compose stays SQLite for one release cycle, then flips after the migration playbook is exercised in production.

### 2. compose.yml: `db` service

Add a `db` service depended on by both `dashboard` and `agent`:

```yaml
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

secrets:
  db_password:
    file: ./.secrets/db_password
```

Both app services gain `depends_on: db: { condition: service_healthy }`. The `dashboard` service env adds:

```yaml
STATION_DB_URL: "postgresql+asyncpg://station:${DB_PASSWORD}@db:5432/station"
```

The named SQLite volume (`station-data`) stays — config files, credentials, vision caches, workspace state still live there. Only the `.db` file moves to `station-pgdata`.

### 3. Migrations: Alembic

The imperative migrations at `dashboard/backend/app/database.py:36-140` (`_migrate_add_columns` + `index_migrations`) become Alembic revisions:

- `dashboard/backend/alembic/` — new directory with `env.py`, `script.py.mako`, `versions/`.
- One initial revision baselines the schema as it exists today (CREATE TABLE for all `Base.metadata` tables, plus the column ALTERs and CREATE INDEXes collapsed into a flat starting state).
- One revision per existing column-add in the current walker so the history survives review (then squashed for the first Postgres deploy).
- `env.py` runs `Base.metadata` against the configured `STATION_DB_URL`; works for both dialects via SQLAlchemy.
- `app.database.init_db()` runs `alembic upgrade head` at startup instead of calling `_migrate_add_columns`. The function stays as the public API surface; only its body changes.
- `dashboard/backend/migrations/0003_simplify_config_schema.py` (config-JSON migration; not a schema change) keeps its current shape and runs after `alembic upgrade head` as today.

### 4. JSONB for event payloads

Three columns become JSONB on Postgres while staying `TEXT` on SQLite:

- `agent_events.event_data` (`models.py:287`)
- `audit_log.action_detail` (`models.py:268`)
- `runs.employee_report` (`models.py:70`) and `runs.verdict_detail` (`models.py:71`)

Use SQLAlchemy's dialect-aware type:

```python
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON

JsonType = JSON().with_variant(JSONB(), "postgresql")

event_data = Column(JsonType, nullable=False)
```

Read paths (`json.loads` of the column value) can be deleted on the Postgres path — SQLAlchemy returns dicts directly. Keep the `json.loads` for SQLite. Wrap in a tiny helper `decode_event_data(row)` to avoid sprinkling dialect checks.

### 5. LISTEN/NOTIFY replaces polling

Two services are affected:

**`dashboard/backend/app/services/log_importer.py`** — today rescans every 30 s for new run-log files. On Postgres: subscribe to a `run_event` channel via asyncpg's `add_listener`. Webhook ingestion (`routers/webhook.py`) NOTIFYs after each insert. The rescan poll is kept as a safety net but interval raises to 5 min (acceptance criterion).

**`dashboard/backend/app/services/stale_run_reaper.py`** — today ticks every 15 s. Postgres path subscribes to a `heartbeat` channel that's NOTIFYed whenever a `runs.last_event_at` column is updated. The 15 s poll stays as the safety net.

Implementation lives in `dashboard/backend/app/services/pubsub.py` (new):

```python
async def listen(channel: str) -> AsyncIterator[dict]:
    """Yield NOTIFY payloads on `channel`. Falls back to a no-op
    iterator on SQLite — callers degrade to polling."""

async def notify(channel: str, payload: dict) -> None:
    """Emit a NOTIFY on Postgres; no-op on SQLite."""
```

Webhook ingestion calls `notify("run_event", {"run_id": ..., "kind": ...})` after each insert. Lifecycle transitions in `services/run_lifecycle.py` call `notify("heartbeat", {"run_id": ...})` after `last_event_at` bumps.

### 6. Connection pooling

`asyncpg` defaults are conservative. Sized at `pool_size=20, max_overflow=10` per app process — well under Postgres' default `max_connections=100`. Per-project containers ([[2026-05-14-issue-386-per-project-containers]]) will open additional pools; tune after that lands.

### 7. Migration script: `scripts/migrate_sqlite_to_postgres.py`

One-shot CLI:

1. Open the SQLite source read-only.
2. Open the Postgres target via `STATION_DB_URL`.
3. For each table in `Base.metadata.sorted_tables` (FK order):
   - `SELECT * FROM <table>` from SQLite, batch 1000 rows.
   - `INSERT INTO <table> ...` against Postgres. Use `ON CONFLICT DO NOTHING` for idempotence.
   - For JSON-text columns, parse + re-emit as JSONB.
4. After all tables, reset Postgres sequences to `MAX(id)+1` per table.
5. Print row-count parity table for operator verification.
6. Exit non-zero on any mismatch.

Operator playbook (added to `docs/configuration.md`):

```text
# 1. Backup
$ docker compose exec dashboard sqlite3 /var/lib/claude-agent-station/station.db \
    ".backup /var/lib/claude-agent-station/station.db.bak"
# 2. Bring up Postgres, leave apps stopped
$ docker compose up -d db
# 3. Run the converter against a stopped app
$ docker compose run --rm dashboard python -m scripts.migrate_sqlite_to_postgres \
    --sqlite /var/lib/claude-agent-station/station.db \
    --postgres "$STATION_DB_URL"
# 4. Verify row counts (printed by the converter)
# 5. Restart apps with STATION_DB_URL set
# 6. Smoke: trigger a run, check the dashboard
# Rollback: unset STATION_DB_URL, restart with the SQLite path. Postgres data
# diverges from that point — re-running the converter clobbers it.
```

### 8. Tests

`tests/conftest.py` already parametrizes around `db_path`. Extend to parametrize on `db_url`:

```python
@pytest.fixture(params=["sqlite", "postgres"])
async def engine(request, postgres_url):
    if request.param == "sqlite":
        yield create_async_engine("sqlite+aiosqlite:///:memory:")
    else:
        yield create_async_engine(postgres_url)
```

CI runs both. `postgres_url` is provided by a `pytest-docker` fixture spinning up `postgres:16-alpine`. Tests covering LISTEN/NOTIFY skip on SQLite via `pytest.mark.postgres_only`.

## Acceptance criteria

From the issue body, expanded:

- [ ] **`STATION_DB_URL` env var supported; existing `STATION_DB_PATH` becomes a SQLite-only fallback.** `Settings.resolved_db_url` returns the new var first, falls back to the SQLite path. Documented in `docs/configuration.md`.
- [ ] **`compose.yml` declares `db` service; `cas-dashboard` + `cas-agent` depend on it.** Healthcheck gate via `depends_on: condition: service_healthy`. Password injected via Docker secret.
- [ ] **All migrations replayable against an empty Postgres → identical schema.** Alembic upgrade against a fresh Postgres produces a schema isomorphic to `Base.metadata.create_all` + every column / index from `_migrate_add_columns`. Verified by a `tests/test_migrations.py::test_alembic_full_schema` test.
- [ ] **Existing SQLite data exportable to Postgres via `scripts/migrate_sqlite_to_postgres.py`.** CLI runs idempotently, prints row-count parity per table, exits non-zero on mismatch.
- [ ] **All 200+ pytest tests pass against both backends (parametrize `STATION_DB_URL`).** CI matrix includes both. SQLite-only tests use `pytest.mark.sqlite_only` (rare); Postgres-only tests use `pytest.mark.postgres_only` (LISTEN/NOTIFY).
- [ ] **`log_importer` polling interval can be raised to 5 min without losing recency (LISTEN/NOTIFY carries the load).** Default poll interval moves from 30 s to 300 s on Postgres; webhook handlers NOTIFY `run_event` on insert. End-to-end test asserts a `run_event` NOTIFY is observed within 1 s of webhook ingestion.
- [ ] **Documented operator migration playbook: backup SQLite, run the converter, verify row counts match, swap env var.** Lives in `docs/configuration.md` under a "Database migration" section. Rollback section included.
- [ ] **No regression: `run-20260513T151408Z`-equivalent end-to-end smoke test passes on Postgres.** A canned `tests/integration/test_run_e2e.py` that triggers a synthetic run, ingests webhooks, exercises the verdict path. Passes against both backends.

## Dependencies / Blocks

- **Blocks** [[2026-05-14-issue-386-per-project-containers]] — multi-writer is the prerequisite for per-project containers. #386 cannot ship until #393 is in production.
- **Independent of** [[2026-05-14-issue-388-approve-integration-verdict]] and [[2026-05-14-issue-387-run-timeline-api]] — both work fine against either backend.
- **Enables** Tier 3 / issue #391 (run decomposition) by way of #386.
- **No upstream dependency** — can land before any other Tier 2 item.

## Risks and rollback

- **Migration downtime.** SQLite → Postgres needs an offline dump + import. Plan for ~5 min downtime on the typical station size. Document in the playbook. Communicate via the dashboard's `StationControl.global_pause` flag pre-cutover.
- **Test parametrization adds CI time.** Postgres fixture adds ~15% to a full test run. Acceptable; document the trade-off in `docs/development.md`.
- **Connection pool exhaustion under multi-runner load.** Default `pool_size=20, max_overflow=10` per process. Until #386 lands, only `cas-dashboard` + `cas-agent` connect — well within budget. Re-tune when per-project containers go live.
- **JSONB read-path divergence.** SQLAlchemy returns dicts on Postgres and strings on SQLite for the same column. A central `decode_event_data` helper avoids per-call-site dialect checks; missing one is a latent bug. Audit the codebase for `json.loads(*.event_data)`, `json.loads(*.action_detail)`, `json.loads(*.employee_report)` before merge.
- **asyncpg + alembic interaction.** Alembic's `env.py` is conventionally sync; for asyncpg we need the `async` env recipe. Documented in the Alembic docs; ship the example `env.py` rather than rolling our own.
- **Rollback**: revert the env var to point at SQLite; the file is unchanged unless the operator ran the converter. The Postgres data diverges as soon as new writes hit it, so rollback only works in the migration window. After that window, rollback is "re-export from Postgres back to SQLite" — out of scope here.

## Test strategy

- **Unit (`tests/test_database.py`)**: connection setup against both URL shapes; `PRAGMA` listener is a no-op on Postgres.
- **Migration (`tests/test_migrations.py`)**: `alembic upgrade head` against an empty Postgres produces a schema isomorphic to the SQLite baseline. Round-trip test: SQLite → converter → Postgres → row-count parity per table.
- **Pub/sub (`tests/test_pubsub.py`, postgres-only)**: emit a NOTIFY, assert the listener yields the payload within 1 s.
- **End-to-end (`tests/integration/test_run_e2e.py`)**: triggers a synthetic run, webhooks land, verdict executes. Parametrized across SQLite and Postgres.
- **Manual cutover rehearsal**: on a staging copy of the prod SQLite file, run the converter, verify row counts, swap env var, smoke-test a run. Document the wall-clock duration so the production cutover gets a realistic window.

## Notes

- The issue body's compose snippet uses `postgresql+asyncpg://station:station@db:5432/station` as the *default* — that hardcodes a weak password. This spec moves the password to a Docker secret so it isn't baked into compose. Flagged here because the issue body's example would otherwise be transcribed as-is.
- `dashboard/backend/migrations/` already exists with one file (`0003_simplify_config_schema.py`); Alembic's `versions/` dir lives alongside, not inside, to avoid confusing the existing numeric-prefix convention. Existing file stays put — it's a JSON-config migration, not a schema one.
- Tier 1A / 1B (run-manager.sh → Python milestone, [[2026-05-11-run-lifecycle-overhaul-design]] Item 5) is *not* a prerequisite — the SQL access patterns are identical from Python or bash via `queue_api`.
