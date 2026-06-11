"""Add is_company_payment flag to payment_methods.

Marks payment methods that represent direct company expenditure — e.g. the
company's own legal-name method seeded at company creation. Invoices paid via
a flagged method count toward the project "spent by company" total alongside
refunded M&S expenses.

Backfill: existing builtin rows whose label matches the owning company's
legal_name case-insensitively are flagged true. Cash and custom methods
remain false.

Revision ID: f3a8b2c1d9e4
Revises: a1c3e5f7b2d4
Create Date: 2026-06-11 13:16:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f3a8b2c1d9e4"
down_revision = "a1c3e5f7b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_methods",
        sa.Column(
            "is_company_payment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Backfill: flag the company-legal-name builtin for every existing company.
    # Soft-deleted (is_active=false) rows are intentionally included so the flag
    # is accurate even if the method was later deactivated.
    op.execute(
        """
        UPDATE payment_methods pm
        SET is_company_payment = true
        FROM companies c
        WHERE pm.company_id = c.id
          AND pm.is_builtin = true
          AND lower(pm.label) = lower(c.legal_name)
        """
    )


def downgrade() -> None:
    op.drop_column("payment_methods", "is_company_payment")
