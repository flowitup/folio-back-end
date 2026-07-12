"""Re-attribute bank-refunded rows with a linked refund invoice to 'both'.

The refunded_by backfill (3c8eef064050) collapsed refunded expenses that had a
linked refund invoice to 'bank'. Under the pre-column semantics those rows were
company-refunded (refundable_status='refunded') AND bank-linked — i.e. refunded
by both sides. Now that 'both' is a valid refunded_by value, restore it for
exactly that population.

Enum values are compared as text (see 3c8eef064050): PostgreSQL forbids using
an enum value inside the same transaction that added it, and fresh databases
run the whole migration chain in one transaction.

Revision ID: a1d4f7b20c93
Revises: 3c8eef064050
Create Date: 2026-07-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1d4f7b20c93"
down_revision = "3c8eef064050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE invoices
            SET refunded_by = 'both'
            WHERE type::text = 'materials_services'
              AND refundable_status = 'refunded'
              AND refunded_by = 'bank'
              AND id IN (
                  SELECT DISTINCT refunds_invoice_id
                  FROM invoices
                  WHERE type::text = 'refund' AND refunds_invoice_id IS NOT NULL
              )
            """
        )
    )


def downgrade() -> None:
    # Reverse of upgrade: 'both' collapses back to 'bank' for the same
    # population. 'both' values set by users on rows WITHOUT a linked refund
    # invoice are also collapsed — to 'company', matching the pre-'both'
    # constraint that refunded_by is single-valued.
    op.execute(
        sa.text(
            """
            UPDATE invoices
            SET refunded_by = 'bank'
            WHERE type::text = 'materials_services'
              AND refundable_status = 'refunded'
              AND refunded_by = 'both'
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
              AND refunded_by = 'both'
            """
        )
    )
