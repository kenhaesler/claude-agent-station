"""Add Run.run_kind / parent_run_id / split_decision_json (#391).

Revision ID: 0004_run_kind_parent
Revises: 0003_runner_quotas
Create Date: 2026-05-15

Adds three columns to ``runs`` for the issue-splitter pipeline:

- ``run_kind`` (Text, nullable): tag the run shape ("primary",
  "sub-of-<N>", "split-decision"). NULL for legacy rows pre-#391.
- ``parent_run_id`` (Text, nullable, indexed): FK to the parent run
  for sub-runs. Indexed because the dashboard tree-view endpoint
  scans by this column.
- ``split_decision_json`` (JSON / JSONB via JsonType in the model):
  archived splitter proposal payload.

NOTE: 0001_baseline calls ``Base.metadata.create_all(checkfirst=True)``,
so on a fresh database the columns may already exist after baseline.
Each ADD COLUMN is guarded with an existence check so this revision is
safe whether the DB was created fresh or upgraded from an older schema.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_run_kind_parent"
down_revision = "0003_runner_quotas"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Return True if *column* already exists in *table* (dialect-agnostic)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _index_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    if not _column_exists("runs", "run_kind"):
        with op.batch_alter_table("runs") as batch:
            batch.add_column(sa.Column("run_kind", sa.Text(), nullable=True))
    if not _column_exists("runs", "parent_run_id"):
        with op.batch_alter_table("runs") as batch:
            batch.add_column(sa.Column("parent_run_id", sa.Text(), nullable=True))
    if not _column_exists("runs", "split_decision_json"):
        with op.batch_alter_table("runs") as batch:
            batch.add_column(sa.Column("split_decision_json", sa.JSON(), nullable=True))
    if not _index_exists("runs", "ix_runs_parent_run_id"):
        op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])


def downgrade() -> None:
    if _index_exists("runs", "ix_runs_parent_run_id"):
        op.drop_index("ix_runs_parent_run_id", "runs")
    if _column_exists("runs", "split_decision_json"):
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("split_decision_json")
    if _column_exists("runs", "parent_run_id"):
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("parent_run_id")
    if _column_exists("runs", "run_kind"):
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("run_kind")
