"""add budget and budget_source to projects

Revision ID: a2b3c4d5e6f7
Revises: ecbe5dd29ded
Create Date: 2026-06-28 01:08:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "ecbe5dd29ded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable budget (NUMERIC 14,2) and budget_source (VARCHAR 120) to projects."""
    op.add_column("projects", sa.Column("budget", sa.Numeric(14, 2), nullable=True))
    op.add_column("projects", sa.Column("budget_source", sa.String(120), nullable=True))


def downgrade() -> None:
    """Remove budget and budget_source from projects."""
    op.drop_column("projects", "budget_source")
    op.drop_column("projects", "budget")
