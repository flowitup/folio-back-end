"""add worker_rate_changes table

Adds an effective-dated daily-rate timeline per worker.  The rate applicable
on date D for worker W is the row with the greatest effective_date <= D in
this table; if no row exists, callers fall back to ``workers.daily_rate``.
Additive only — existing data is unchanged and no backfill is required.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e4f5a6b7c8d9"
down_revision = "1144dd90e533"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_rate_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "worker_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("daily_rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_worker_rate_changes_worker_id",
        "worker_rate_changes",
        ["worker_id"],
    )
    op.create_unique_constraint(
        "uq_worker_rate_effective",
        "worker_rate_changes",
        ["worker_id", "effective_date"],
    )
    op.create_index(
        "ix_worker_rate_changes_worker_date",
        "worker_rate_changes",
        ["worker_id", "effective_date"],
    )


def downgrade() -> None:
    op.drop_table("worker_rate_changes")
