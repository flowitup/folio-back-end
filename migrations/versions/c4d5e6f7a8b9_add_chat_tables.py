"""add chat tables (team chat, feature-flagged)

chat_messages: one row per message in a virtual company / project channel.
chat_channel_reads: per-user read marker per channel.

Revision ID: c4d5e6f7a8b9
Revises: b7f1c2a4d8e5
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a8b9"
down_revision = "b7f1c2a4d8e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_kind", sa.String(16), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "sender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("attachment_key", sa.Text(), nullable=True),
        sa.Column("attachment_filename", sa.String(255), nullable=True),
        sa.Column("attachment_content_type", sa.String(100), nullable=True),
        sa.Column("attachment_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_chat_messages_channel_created",
        "chat_messages",
        ["channel_kind", "channel_id", "created_at"],
    )
    op.create_table(
        "chat_channel_reads",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel_kind", sa.String(16), primary_key=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_channel_reads")
    op.drop_index("ix_chat_messages_channel_created", table_name="chat_messages")
    op.drop_table("chat_messages")
