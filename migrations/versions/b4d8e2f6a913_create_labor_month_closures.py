"""create_labor_month_closures

"Chốt tháng" (owner decision QĐ-10): a manager closes a project's labor month once
its wages are settled. Not a lock: later writes into the month still succeed and
are recorded in labor_month_closure_edits (who, when, what kind, which entry/worker).

Revision ID: b4d8e2f6a913
Revises: a7c3e91d5b24
Create Date: 2026-11-20 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4d8e2f6a913"
down_revision = "a7c3e91d5b24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "labor_month_closures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "closed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "month", name="uq_labor_month_closures_project_month"),
    )
    op.create_index("ix_labor_month_closures_project_id", "labor_month_closures", ["project_id"])

    op.create_table(
        "labor_month_closure_edits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "closure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("labor_month_closures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entry_date", sa.Date(), nullable=True),
    )
    op.create_index("ix_labor_month_closure_edits_closure_id", "labor_month_closure_edits", ["closure_id"])


def downgrade():
    op.drop_index("ix_labor_month_closure_edits_closure_id", table_name="labor_month_closure_edits")
    op.drop_table("labor_month_closure_edits")
    op.drop_index("ix_labor_month_closures_project_id", table_name="labor_month_closures")
    op.drop_table("labor_month_closures")
