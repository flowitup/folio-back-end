"""Add service_month column to invoices for labor payment-month tracking.

Invoices of type labor can optionally record the calendar month the payment
covers, always normalized to the first day of the month. NULL for all other
invoice types and for labor invoices where the month is not tracked.

Revision ID: 925c1d70fc82
Revises: d4e7f1a9c2b8
Create Date: 2026-07-03 00:35:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "925c1d70fc82"
down_revision = "d4e7f1a9c2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("service_month", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "service_month")
