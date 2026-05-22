"""Backfill runs.autonomy_level column.

Revision ID: 0006_run_autonomy_level
Revises: 0005_vision_chat_attachments
Create Date: 2026-05-22

ADR-0001 added ``Run.autonomy_level`` to the model but no alembic migration
ever defined the column, so DBs created from alembic alone are missing it.
This migration adds the column when absent (idempotent), then backfills
historic rows from the owning project's current setting.

Backfill scope: rows where ``runs.autonomy_level`` is NULL or equals the
stale server-side default ``'assisted'``. Rows that already carry ``'auto'``
or ``'manual'`` are left alone — those were set deliberately by a per-run
override and we don't want to clobber them.

Caveat: this uses the project's *current* ``autonomy_level``, not the value
in force when the run ran. The historic value was never recorded anywhere,
so the current project setting is the best signal we have. If a project's
level was changed after a run, that run will be labelled with the new
level. That's acceptable — the alternative is leaving every historic
FULL-AUTO run mislabelled as ASSIST.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_run_autonomy_level"
down_revision = "0005_vision_chat_attachments"
branch_labels = None
depends_on = None


def _column_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    if not _column_exists("runs", "autonomy_level"):
        op.add_column("runs", sa.Column("autonomy_level", sa.Text(), nullable=True))

    # Backfill from the owning project's current setting. Single UPDATE with
    # a correlated subquery so it works on both SQLite and Postgres without
    # dialect-specific UPDATE...FROM syntax.
    op.execute(
        sa.text(
            """
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
        )
    )


def downgrade() -> None:
    if _column_exists("runs", "autonomy_level"):
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("autonomy_level")
