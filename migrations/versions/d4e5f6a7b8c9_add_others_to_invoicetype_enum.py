"""add others to invoicetype enum

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-26 12:00:00.000000

Adds a fourth invoice type 'others' for miscellaneous expenses
(restaurants, entertainment, travel, etc.).
"""

from alembic import op


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE invoicetype ADD VALUE IF NOT EXISTS 'others'")


def downgrade() -> None:
    op.execute("DELETE FROM invoices WHERE type = 'others'")
    op.execute(
        """
        CREATE TYPE invoicetype_old AS ENUM ('released_funds', 'labor', 'materials_services');
        ALTER TABLE invoices ALTER COLUMN type TYPE invoicetype_old USING type::text::invoicetype_old;
        DROP TYPE invoicetype;
        ALTER TYPE invoicetype_old RENAME TO invoicetype;
    """
    )
