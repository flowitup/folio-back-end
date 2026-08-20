"""unlock_cash_payment_method

"Cash" was seeded as a builtin payment method for every company, and builtin
rows are protected from deletion and deactivation. Companies that never handle
cash had no way to get rid of it.

New companies no longer get a Cash row at all. This clears the builtin flag on
the ones already seeded so their admins can remove them from the settings
screen if they want to. The rows stay active and keep their id, so invoices
that reference them are untouched — nothing disappears unless a human deletes
it.

Every builtin labelled 'Cash' is unlocked. The company's own legal-name builtin
keeps its protection: under the seed that produced these rows, the legal-name
row was skipped whenever the name matched 'Cash', so no legal-name builtin
carries that label. Methods users created by hand were never builtin and are
not matched.

Revision ID: c7e1a94db302
Revises: b64c8e1f27a3
Create Date: 2026-08-20 02:40:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c7e1a94db302"
down_revision = "b64c8e1f27a3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE payment_methods
        SET is_builtin = false
        WHERE is_builtin = true
          AND lower(btrim(label)) = 'cash'
        """
    )


def downgrade():
    # Re-protect only rows that still look like the original seed: a company
    # never toggled them into a company or personal payment method, and users
    # never marked their own methods builtin.
    op.execute(
        """
        UPDATE payment_methods
        SET is_builtin = true
        WHERE is_builtin = false
          AND is_company_payment = false
          AND is_personal_payment = false
          AND lower(btrim(label)) = 'cash'
        """
    )
