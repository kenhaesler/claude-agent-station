"""Convert all DateTime columns to TIMESTAMP WITH TIME ZONE (#414).

On Postgres, bare ``DateTime`` maps to ``TIMESTAMP WITHOUT TIME ZONE``.
``DateTime(timezone=True)`` maps to ``TIMESTAMP WITH TIME ZONE``.  This
revision ALTERs every affected column in the existing schema so that
tz-aware datetimes can be stored without Postgres rejecting them.

SQLite does not distinguish the two variants, so the migration is a no-op
on SQLite (dialect guard below).

Revision ID: 0002_datetime_timezone
Revises: 0001_baseline
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_datetime_timezone"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# (table, column) pairs that need converting.
# Generated from models.py — every Column(DateTime(timezone=True)) that was
# previously Column(DateTime) before this PR.
_COLUMNS: list[tuple[str, str]] = [
    ("projects", "vision_cached_at"),
    ("projects", "created_at"),
    ("projects", "updated_at"),
    ("runs", "started_at"),
    ("runs", "finished_at"),
    ("runs", "last_event_at"),
    ("config", "updated_at"),
    ("plans", "created_at"),
    ("plans", "updated_at"),
    ("coordinator_tasks", "claimed_at"),
    ("coordinator_tasks", "created_at"),
    ("coordinator_tasks", "started_at"),
    ("coordinator_tasks", "finished_at"),
    ("coordinator_messages", "created_at"),
    ("notifications", "created_at"),
    ("task_queue", "created_at"),
    ("task_queue", "updated_at"),
    ("task_queue", "assigned_at"),
    ("task_queue", "started_at"),
    ("task_queue", "completed_at"),
    ("plan_usage_history", "created_at"),
    ("audit_log", "started_at"),
    ("audit_log", "finished_at"),
    ("agent_events", "created_at"),
    ("conflict_resolutions", "started_at"),
    ("conflict_resolutions", "finished_at"),
    ("task_outcomes", "created_at"),
    ("brainstorm_sessions", "created_at"),
    ("brainstorm_sessions", "updated_at"),
    ("brainstorm_messages", "created_at"),
    ("integration_features", "created_at"),
    ("integration_features", "updated_at"),
    ("prompt_versions", "created_at"),
    ("permission_requests", "created_at"),
    ("permission_requests", "resolved_at"),
    ("run_controls", "requested_at"),
    ("run_controls", "consumed_at"),
    ("station_control", "updated_at"),
    ("vision_chat_sessions", "created_at"),
    ("vision_chat_sessions", "updated_at"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite: no-op

    for table, column in _COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE "
                f"USING {column} AT TIME ZONE 'UTC'"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, column in _COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE "
                f"USING {column} AT TIME ZONE 'UTC'"
            )
        )
