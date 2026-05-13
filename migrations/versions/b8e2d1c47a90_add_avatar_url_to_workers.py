"""add avatar_url to workers

Revision ID: b8e2d1c47a90
Revises: a3f7b8c9d0e1
Create Date: 2026-05-13 11:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "b8e2d1c47a90"
down_revision = "a3f7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workers", "avatar_url")
