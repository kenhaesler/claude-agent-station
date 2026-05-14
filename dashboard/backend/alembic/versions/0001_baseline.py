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
    # SQLite and Postgres (Postgres >= 9.5).
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
