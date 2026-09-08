"""notification preferences: per-user push opt-outs

Revision ID: b3c7d1a9e254
Revises: c2b8f1a0d743
Create Date: 2026-09-08

No backfill: a missing row means every notification is on, so existing users keep their
current behaviour and only an explicit opt-out writes a row.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b3c7d1a9e254"
down_revision = "c2b8f1a0d743"
branch_labels = None
depends_on = None

_FLAGS = ("push_enabled", "chat", "attendance", "tasks", "membership", "billing")


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        *[sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("true")) for name in _FLAGS],
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
