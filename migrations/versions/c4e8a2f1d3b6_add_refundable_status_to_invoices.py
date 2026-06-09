"""Add refundable_status column to invoices for company-scoped refund tracking.

Invoices of type materials_services can be marked with a nullable refund lifecycle
status (refundable / refund_pending / refunded). NULL means not marked refundable.

Revision ID: c4e8a2f1d3b6
Revises: a1c3e5f7b9d2
Create Date: 2026-06-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e8a2f1d3b6"
down_revision = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("refundable_status", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "refundable_status")
