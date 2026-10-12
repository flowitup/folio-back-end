"""chat_message_reactions (one emoji per person per message)

Revision ID: e5a9c3d7f1b2
Revises: d4f8b2c6e0a3
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5a9c3d7f1b2"
down_revision = "d4f8b2c6e0a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_message_reactions",
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("emoji", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_message_reactions")
