"""add workers and labor_entries tables

Revision ID: a1b2c3d4e5f6
Revises: 6689f8c8b051
Create Date: 2026-02-01 23:40:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "6689f8c8b051"
branch_labels = None
depends_on = None


def upgrade():
    # Create workers table
    op.create_table(
        "workers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("daily_rate", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workers_project_id", "workers", ["project_id"], unique=False)

    # Create labor_entries table
    op.create_table(
        "labor_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_override", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_id", "date", name="uq_worker_date"),
    )
    op.create_index("ix_labor_entries_worker_id", "labor_entries", ["worker_id"], unique=False)


def downgrade():
    op.drop_index("ix_labor_entries_worker_id", table_name="labor_entries")
    op.drop_table("labor_entries")
    op.drop_index("ix_workers_project_id", table_name="workers")
    op.drop_table("workers")
