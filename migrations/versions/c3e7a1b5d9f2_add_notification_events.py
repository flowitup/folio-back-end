"""notification_events (the in-app feed behind the bell: one row per notified user)

Revision ID: c3e7a1b5d9f2
Revises: b2d6f0a4c8e1
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3e7a1b5d9f2"
down_revision = "b2d6f0a4c8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("texts", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notification_events_user_created", "notification_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_events_user_created", table_name="notification_events")
    op.drop_table("notification_events")
