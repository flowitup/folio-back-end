"""rename invoicetype enum value client -> released_funds

Revision ID: d7e9a1b2c3f4
Revises: fe343de24e08
Create Date: 2026-05-17 14:00:00.000000

Released-funds terminology better reflects the source of money that funds
the project (bank draw, savings release, etc.) rather than a client paying.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "d7e9a1b2c3f4"
down_revision = "fe343de24e08"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres 10+: rename enum value in place (preserves rows referencing it).
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'client' TO 'released_funds'")


def downgrade():
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'released_funds' TO 'client'")
