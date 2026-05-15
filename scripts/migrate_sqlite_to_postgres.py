"""One-shot SQLite -> Postgres converter (#393).

Usage:
    python -m scripts.migrate_sqlite_to_postgres \
        --sqlite /var/lib/claude-agent-station/station.db \
        --postgres "postgresql+asyncpg://station:pw@db:5432/station"

Operator playbook: see "SQLite → Postgres migration playbook" in
``docs/configuration.md`` for the stop-services / backup / alembic /
migrate / switch-backend / verify / rollback runbook.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow ``python -m scripts.migrate_sqlite_to_postgres`` to import the
# ``app`` package that lives under ``dashboard/backend/``. The migrator is
# intentionally launched from the repo root so the scripts package is on
# sys.path, but ``app`` requires its own entry — add it here so the
# subprocess works regardless of how the operator invokes it.
_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "dashboard" / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import Integer, func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

# Three columns that hold JSON-as-text on SQLite and JSONB on Postgres.
_JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "agent_events": ("event_data",),
    "audit_log": ("action_detail",),
    "runs": ("employee_report", "verdict_detail"),
}

logger = logging.getLogger("migrate_sqlite_to_postgres")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "One-shot SQLite -> Postgres data converter for "
            "claude-agent-station (#393). Stop the agent + dashboard before "
            "running."
        ),
        epilog=(
            "Full runbook (backup, alembic, switch-backend, verify, "
            "rollback) lives in docs/configuration.md under "
            '"SQLite → Postgres migration playbook".'
        ),
    )
    p.add_argument("--sqlite", required=True, help="Path to source SQLite file")
    p.add_argument("--postgres", required=True, help="Target SQLAlchemy URL")
    p.add_argument(
        "--batch", type=int, default=1000,
        help=(
            "Rows per INSERT batch. Each batch commits independently so "
            "the destination transaction log stays bounded on multi-million-"
            "row migrations (default: 1000)."
        ),
    )
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


async def _copy_table(src_engine, dst_engine, table, batch_size: int = 1000) -> tuple[int, int]:
    async with src_engine.connect() as src_conn:
        result = await src_conn.execute(select(table))
        src_rows = result.mappings().all()
    if not src_rows:
        return (0, 0)

    transformed = [_transform_row(table.name, dict(r)) for r in src_rows]
    # Use the SQLAlchemy Table object directly so no identifier is interpolated
    # into a raw SQL string — this is injection-safe even though the table names
    # are internal constants from Base.metadata.
    insert_stmt = pg_insert(table).on_conflict_do_nothing()
    inserted = 0
    # Commit per batch so the Postgres transaction log stays bounded on
    # multi-million-row migrations. A single ``async with dst_engine.begin()``
    # around the whole loop would hold one transaction open for the entire
    # table copy.
    for i in range(0, len(transformed), batch_size):
        batch = transformed[i : i + batch_size]
        async with dst_engine.begin() as dst_conn:
            await dst_conn.execute(insert_stmt, batch)
        inserted += len(batch)
    return (len(src_rows), inserted)


async def _reset_sequences(dst_engine, tables: list) -> None:
    """Advance each SERIAL sequence to max(id) so INSERT won't collide.

    Uses sa.func.* throughout — no table or column name is interpolated into
    a raw SQL string, so this is injection-safe even in the presence of
    unusual table names.

    Per-table failures are logged but do not abort the migration: a table
    whose ``id`` PK is not SERIAL (text UUID, etc.) raises here, which is
    fine. A privilege error is a real problem and would otherwise produce
    silent ``id`` collisions on the next real INSERT — logging the warning
    surfaces it.
    """
    async with dst_engine.begin() as conn:
        for table in tables:
            # Only attempt reset for tables that have an integer 'id' column.
            id_col = table.c.get("id")
            if id_col is None or not isinstance(id_col.type, Integer):
                continue
            try:
                seq_name = func.pg_get_serial_sequence(table.name, "id")
                max_id = func.coalesce(func.max(id_col).cast(Integer), 1)
                await conn.execute(select(func.setval(seq_name, max_id)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "sequence reset for %s skipped: %s",
                    table.name, exc,
                )


async def _async_main(args: argparse.Namespace) -> int:
    from app.database import Base
    import app.models  # noqa: F401

    src_url = f"sqlite+aiosqlite:///{args.sqlite}"
    src_engine = create_async_engine(src_url)
    dst_engine = create_async_engine(args.postgres)

    mismatches: list[str] = []
    summary: list[tuple[str, int, int]] = []
    for table in Base.metadata.sorted_tables:
        src_count, inserted = await _copy_table(
            src_engine, dst_engine, table, batch_size=args.batch,
        )
        summary.append((table.name, src_count, inserted))
        if src_count != inserted:
            mismatches.append(f"{table.name}: {inserted}/{src_count}")

    # Pass Table objects, not names — _reset_sequences inspects each
    # table's columns to find the id/Integer PK (string list lacks .c).
    await _reset_sequences(dst_engine, list(Base.metadata.sorted_tables))
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
