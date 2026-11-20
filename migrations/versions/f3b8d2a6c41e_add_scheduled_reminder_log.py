"""scheduled_reminder_log (scheduled reminder pushes already sent, QĐ-9)

One row per (user, reminder kind, Europe/Paris day): makes every reminder send once across
re-runs and lets the job keep to one reminder push per person per day.

Revision ID: f3b8d2a6c41e
Revises: b4d8e2f6a913
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f3b8d2a6c41e"
down_revision = "b4d8e2f6a913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_reminder_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("pushed", sa.Boolean(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "local_date", name="uq_scheduled_reminder_log_user_kind_date"),
    )


def downgrade() -> None:
    op.drop_table("scheduled_reminder_log")
