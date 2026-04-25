"""add_shift_type_to_labor_entries

Revision ID: c1d2e3f4g5h6
Revises: b040af6aad78
Create Date: 2026-04-25 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4g5h6"
down_revision = "b040af6aad78"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("labor_entries", sa.Column("shift_type", sa.String(20), nullable=False, server_default="full"))


def downgrade():
    op.drop_column("labor_entries", "shift_type")
