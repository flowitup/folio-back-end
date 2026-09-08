"""chat push markers: per (user, channel) quiet window

Revision ID: c4d8e2b0f713
Revises: b3c7d1a9e254
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d8e2b0f713"
down_revision = "b3c7d1a9e254"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_push_markers",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel_key", sa.String(64), primary_key=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_push_markers")
