"""rename invoicetype enum value supplier -> materials_services

Revision ID: e8a2c4d6f1b3
Revises: d7e9a1b2c3f4
Create Date: 2026-05-17 21:00:00.000000

Materials & services better reflects that this category covers all third-party
disbursements for construction materials AND service providers (transport,
sub-contracted trades, professional fees, etc.) — not only goods suppliers.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e8a2c4d6f1b3"
down_revision = "d7e9a1b2c3f4"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres 10+: rename enum value in place (preserves rows referencing it).
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'supplier' TO 'materials_services'")


def downgrade():
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'materials_services' TO 'supplier'")
