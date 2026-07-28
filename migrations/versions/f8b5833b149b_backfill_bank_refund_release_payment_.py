"""Backfill payment_method_id/label onto historical bank-refund releases.

The live path (FundsReleaseAdapter.create_bank_refund_release, since this
revision) now copies payment_method_id + payment_method_label from the source
materials_services expense onto the auto-generated released_funds release it
creates, so the release carries the same company-vs-personal attribution as
the expense it reimburses. This backfills every historical bank-refund
release created before that inheritance existed.

Enum values are compared as text (type::text = '...') in the WHERE clause:
PostgreSQL forbids using a bare enum literal against an enum column inside
the same transaction that introduced the value, and a fresh database runs
the whole migration chain in one transaction (see 3c8eef064050 and
7e20d88b8197 for the precedent).

Revision ID: f8b5833b149b
Revises: 121ab5361948
Create Date: 2026-07-28 09:30:00.000000
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "f8b5833b149b"
down_revision = "121ab5361948"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Only auto-generated bank-refund releases (refunds_invoice_id set, is_auto_generated
    # true) with no payment method yet — never touches facture-driven releases (those
    # always have refunds_invoice_id IS NULL) or releases already carrying an attribution
    # (e.g. re-running this migration, or a row already fixed by the live path).
    result = bind.execute(
        text(
            """
            UPDATE invoices AS r
            SET payment_method_id = s.payment_method_id,
                payment_method_label = s.payment_method_label
            FROM invoices AS s
            WHERE r.refunds_invoice_id = s.id
              AND r.type::text = 'released_funds'
              AND r.refunds_invoice_id IS NOT NULL
              AND r.is_auto_generated IS TRUE
              AND r.payment_method_id IS NULL
              AND s.payment_method_id IS NOT NULL
            """
        )
    )
    print(f"[backfill_bank_refund_release_payment_method] rows updated={result.rowcount}")


def downgrade() -> None:
    # Intentional no-op: restoring NULLs would erase legitimate manual attribution a
    # user may have set via the payment-method-only edit carve-out after this backfill
    # ran (UpdateInvoiceUseCase permits editing payment_method_id on auto-generated
    # released_funds releases). There is no way to distinguish "backfilled by this
    # migration" from "since edited by a user" after the fact, so downgrading the
    # schema does not attempt to reverse the data change.
    pass
