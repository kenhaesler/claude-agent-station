"""Backfill runs.autonomy_level column.

Revision ID: 0006_run_autonomy_level
Revises: 0005_vision_chat_attachments
Create Date: 2026-05-22

ADR-0001 added ``Run.autonomy_level`` to the model but no alembic migration
ever defined the column, so DBs created from alembic alone are missing it.
This migration adds the column when absent (idempotent) and drops the stale
``default="assisted"`` server-side — see ``app/models.py``.
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


def downgrade() -> None:
    if _column_exists("runs", "autonomy_level"):
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("autonomy_level")
