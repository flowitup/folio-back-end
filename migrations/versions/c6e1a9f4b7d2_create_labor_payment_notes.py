"""create_labor_payment_notes

One free-text note per (project, worker, month) on the labor Payments tab,
so a manager can explain an unpaid or partly paid month.

Revision ID: c6e1a9f4b7d2
Revises: d7a3c5e9b214
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c6e1a9f4b7d2"
down_revision = "d7a3c5e9b214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labor_payment_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "worker_id", "month", name="uq_labor_payment_notes_project_worker_month"),
    )


def downgrade() -> None:
    op.drop_table("labor_payment_notes")
