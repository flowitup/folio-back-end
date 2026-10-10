"""push_devices.locale (language the app is shown in, so pushes read in it)

Revision ID: b2d6f0a4c8e1
Revises: a1c5e9d3b7f2
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "b2d6f0a4c8e1"
down_revision = "a1c5e9d3b7f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("push_devices", sa.Column("locale", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("push_devices", "locale")
