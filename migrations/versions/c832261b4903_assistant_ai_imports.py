"""assistant_ai_imports

Adds the two extension tables the Folio Assistant uses to record what it did to an
invoice or a library product, without touching either aggregate's own contract:

- ``invoice_ai_imports``: one row per assistant import/attach event on an invoice
  (feature C ticket scan, feature B invoice fetch). Links to the two attachments it
  produced (original photo + generated scan PDF) and the ai_confidence/flags/category
  the pipeline recorded at write time.
- ``assistant_material_imports``: one row per assistant import of a library product
  (feature A). ``photo_sha256`` is unique — it is the pipeline's cache key so the same
  photo never re-runs the identify/search pipeline twice.

Both tables are pure assistant provenance: no other bounded context reads them, and
neither is referenced by any existing FK, so this migration only adds tables.

Revision ID: c832261b4903
Revises: b3d7e2f6a1c9
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c832261b4903"
down_revision = "b3d7e2f6a1c9"
branch_labels = None
depends_on = None

# JSONB on Postgres — matches chat_messages.payload / other assistant JSON columns.
FlagsJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "invoice_ai_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("flags", FlagsJSON, nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("ai_confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("original_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scan_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["original_attachment_id"], ["invoice_attachments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scan_attachment_id"], ["invoice_attachments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_ai_imports_invoice_id", "invoice_ai_imports", ["invoice_id"])

    op.create_table(
        "assistant_material_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("photo_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["bibliotheque_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("photo_sha256"),
    )
    op.create_index("ix_assistant_material_imports_product_id", "assistant_material_imports", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_material_imports_product_id", table_name="assistant_material_imports")
    op.drop_table("assistant_material_imports")
    op.drop_index("ix_invoice_ai_imports_invoice_id", table_name="invoice_ai_imports")
    op.drop_table("invoice_ai_imports")
