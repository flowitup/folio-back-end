"""add invoice_prefix to projects

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-25 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("invoice_prefix", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "invoice_prefix")
