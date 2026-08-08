"""Add applied_to_invoice_id and settled_via to invoices for avoir-purse returns.

A return invoice settled via 'avoir' can be applied as credit toward a future
invoice (materials_services / labor / others) instead of being cashed out.
applied_to_invoice_id links the return to that target invoice; settled_via
records how the return was settled ('cash' | 'avoir'). Both columns are only
meaningful when type == 'return' — enforced at the application layer, not by
a DB constraint, mirroring refunds_invoice_id / refundable_status.

settled_via is a plain VARCHAR with a CHECK constraint rather than a Postgres
enum type — adding new allowed values later needs no ALTER TYPE migration.

Revision ID: 033b9c30eea0
Revises: a3c9d0e47b21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "033b9c30eea0"
down_revision = "a3c9d0e47b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("applied_to_invoice_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_applied_to_invoice_id",
        "invoices",
        "invoices",
        ["applied_to_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_invoices_applied_to_invoice_id", "invoices", ["applied_to_invoice_id"])

    op.add_column("invoices", sa.Column("settled_via", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        "ck_invoices_settled_via",
        "invoices",
        "settled_via IN ('cash', 'avoir')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_settled_via", "invoices", type_="check")
    op.drop_column("invoices", "settled_via")

    op.drop_index("ix_invoices_applied_to_invoice_id", table_name="invoices")
    op.drop_constraint("fk_invoices_applied_to_invoice_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "applied_to_invoice_id")
