"""Worker-requested changes on validated attendance.

A worker may propose a different shift / supplement / note for a day that a manager
already validated. The proposal lives on the row (``proposed_*`` + who/when asked)
and leaves the priced values untouched until a manager validates it (values are
applied) or rejects it (proposal cleared). Pending rows are edited in place instead.

Revision ID: c7d8e9f0a1b2
Revises: b633c91f9fbe
Create Date: 2026-09-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7d8e9f0a1b2"
down_revision = "b633c91f9fbe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labor_entries", sa.Column("proposed_shift_type", sa.String(20), nullable=True))
    op.add_column("labor_entries", sa.Column("proposed_supplement_hours", sa.Integer(), nullable=True))
    op.add_column("labor_entries", sa.Column("proposed_note", sa.String(500), nullable=True))
    op.add_column("labor_entries", sa.Column("change_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "labor_entries",
        sa.Column(
            "change_requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_change_requested_by"),
            nullable=True,
        ),
    )
    # Open change requests are few and read on every bell poll.
    op.create_index(
        "ix_labor_entries_change_requested",
        "labor_entries",
        ["worker_id"],
        postgresql_where=sa.text("change_requested_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_labor_entries_change_requested", table_name="labor_entries")
    op.drop_column("labor_entries", "change_requested_by_user_id")
    op.drop_column("labor_entries", "change_requested_at")
    op.drop_column("labor_entries", "proposed_note")
    op.drop_column("labor_entries", "proposed_supplement_hours")
    op.drop_column("labor_entries", "proposed_shift_type")
