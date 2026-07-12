"""Add refunded_by column to invoices for explicit company/bank refund attribution.

Invoices of type materials_services can record who issued the refund
('company' | 'bank') once refundable_status == 'refunded'. NULL otherwise.

Backfill (order matters):
  1. Any refunded materials_services invoice that has ≥1 linked refund
     invoice (type='refund', refunds_invoice_id pointing to it) is backfilled
     to 'bank' — a supplier/vendor sent the money back.
  2. Any remaining refunded materials_services invoice with refunded_by still
     NULL defaults to 'company' (legacy-compatible: refunds recorded before
     this column existed were tracked as company reimbursements).

Revision ID: 3c8eef064050
Revises: 925c1d70fc82
Create Date: 2026-07-12 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3c8eef064050"
down_revision = "925c1d70fc82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("refunded_by", sa.String(length=10), nullable=True))

    # Enum values are compared as text: 'refund' was added to the invoicetype
    # enum by an earlier migration, and PostgreSQL forbids using a newly-added
    # enum value inside the same transaction that added it (fresh databases run
    # the whole migration chain in one transaction — UnsafeNewEnumValueUsage).
    op.execute(
        sa.text(
            """
            UPDATE invoices
            SET refunded_by = 'bank'
            WHERE type::text = 'materials_services'
              AND refundable_status = 'refunded'
              AND id IN (
                  SELECT DISTINCT refunds_invoice_id
                  FROM invoices
                  WHERE type::text = 'refund' AND refunds_invoice_id IS NOT NULL
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE invoices
            SET refunded_by = 'company'
            WHERE type::text = 'materials_services'
              AND refundable_status = 'refunded'
              AND refunded_by IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("invoices", "refunded_by")
