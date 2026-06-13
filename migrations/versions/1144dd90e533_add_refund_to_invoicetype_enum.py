"""add refund type and refunds_invoice_id link to invoices

Adds invoice type 'refund' (supplier refund / credit note) and a nullable self-FK
refunds_invoice_id linking a refund to the materials_services invoice it refunds.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "1144dd90e533"
down_revision = "f3a8b2c1d9e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE invoicetype ADD VALUE IF NOT EXISTS 'refund'")
    op.add_column(
        "invoices",
        sa.Column("refunds_invoice_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_refunds_invoice_id",
        "invoices",
        "invoices",
        ["refunds_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_invoices_refunds_invoice_id", "invoices", ["refunds_invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_refunds_invoice_id", table_name="invoices")
    op.drop_constraint("fk_invoices_refunds_invoice_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "refunds_invoice_id")
    op.execute("DELETE FROM invoices WHERE type = 'refund'")
    op.execute(
        """
        CREATE TYPE invoicetype_old AS ENUM ('released_funds', 'labor', 'materials_services', 'others');
        ALTER TABLE invoices ALTER COLUMN type TYPE invoicetype_old USING type::text::invoicetype_old;
        DROP TYPE invoicetype;
        ALTER TYPE invoicetype_old RENAME TO invoicetype;
    """
    )
