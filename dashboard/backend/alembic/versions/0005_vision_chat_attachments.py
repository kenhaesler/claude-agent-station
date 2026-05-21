"""Add vision_chat_attachments table.

Revision ID: 0005_vision_chat_attachments
Revises: 0004_run_kind_parent
Create Date: 2026-05-21

Spec: docs/superpowers/specs/2026-05-21-vision-reference-files-design.md.
Stores per-session reference-file metadata + extraction cache.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_vision_chat_attachments"
down_revision = "0004_run_kind_parent"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _index_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    if not _table_exists("vision_chat_attachments"):
        op.create_table(
            "vision_chat_attachments",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "session_id",
                sa.Text(),
                sa.ForeignKey("vision_chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("mime_type", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("disk_path", sa.Text(), nullable=False),
            sa.Column("extracted_text", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _index_exists("vision_chat_attachments", "ix_vision_chat_attachments_session_id"):
        op.create_index(
            "ix_vision_chat_attachments_session_id",
            "vision_chat_attachments",
            ["session_id"],
        )


def downgrade() -> None:
    if _index_exists("vision_chat_attachments", "ix_vision_chat_attachments_session_id"):
        op.drop_index(
            "ix_vision_chat_attachments_session_id", "vision_chat_attachments",
        )
    if _table_exists("vision_chat_attachments"):
        op.drop_table("vision_chat_attachments")
