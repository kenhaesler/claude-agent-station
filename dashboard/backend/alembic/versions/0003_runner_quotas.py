"""Add Project.runner_memory_limit / runner_cpu_limit (#386).

Revision ID: 0003_runner_quotas
Revises: 0002_datetime_timezone
Create Date: 2026-05-15

runner_memory_limit: Integer, bytes. Per-project Docker --memory value. NULL = compose default.
runner_cpu_limit: Float, fractional CPUs. Per-project Docker --cpus value. NULL = compose default.

NOTE: 0001_baseline calls Base.metadata.create_all(checkfirst=True), which means on a fresh
database the columns already exist after baseline. This revision guards each ADD COLUMN with
an existence check so it is safe whether the DB was created fresh (via baseline) or upgraded
from an older schema that lacked the columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_runner_quotas"
down_revision = "0002_datetime_timezone"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Return True if *column* already exists in *table* (dialect-agnostic)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _column_exists("projects", "runner_memory_limit"):
        with op.batch_alter_table("projects") as batch:
            batch.add_column(sa.Column("runner_memory_limit", sa.Integer(), nullable=True))
    if not _column_exists("projects", "runner_cpu_limit"):
        with op.batch_alter_table("projects") as batch:
            batch.add_column(sa.Column("runner_cpu_limit", sa.Float(), nullable=True))


def downgrade() -> None:
    if _column_exists("projects", "runner_cpu_limit"):
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("runner_cpu_limit")
    if _column_exists("projects", "runner_memory_limit"):
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("runner_memory_limit")
