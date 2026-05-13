"""add avatar_url to workers

Revision ID: b8e2d1c47a90
Revises: a3f7b8c9d0e1
Create Date: 2026-05-13 11:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "b8e2d1c47a90"
# Chained after the persons + company-scoping migration that landed on
# master in parallel with this one. Both originally pointed at
# a3f7b8c9d0e1 which produced "multiple head revisions" at deploy time;
# re-pointing here keeps the chain linear.
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workers", "avatar_url")
