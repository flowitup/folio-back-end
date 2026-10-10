"""companies.hidden_sections (navigation sections a company hides for its members)

Revision ID: a1c5e9d3b7f2
Revises: e4b7c2d9a615
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c5e9d3b7f2"
down_revision = "e4b7c2d9a615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("hidden_sections", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("companies", "hidden_sections")
