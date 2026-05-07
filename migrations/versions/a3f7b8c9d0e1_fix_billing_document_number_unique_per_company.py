"""fix billing_document unique per (company_id, kind, document_number)

Revision ID: a3f7b8c9d0e1
Revises: 2d9c35848b9b
Create Date: 2026-05-07 00:00:00.000000

SUMMARY
-------
Fixes the document_number uniqueness collision across companies for the same
user (review finding C1).

Before: UNIQUE (user_id, kind, document_number)
  → two companies owned by same user both produce DEV-2026-001 → IntegrityError.

After:  UNIQUE (company_id, kind, document_number) WHERE company_id IS NOT NULL
  → uniqueness scoped to company; legacy NULL company_id rows are excluded from
    the constraint via partial index (they were unique by user before migration).

The old constraint (uq_billing_document_user_kind_number) is dropped first.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a3f7b8c9d0e1"
down_revision = "2d9c35848b9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Drop the old per-user unique constraint
    # ------------------------------------------------------------------
    op.drop_constraint(
        "uq_billing_document_user_kind_number",
        "billing_documents",
        type_="unique",
    )

    # ------------------------------------------------------------------
    # Add per-company partial unique index (WHERE company_id IS NOT NULL)
    # Legacy rows with NULL company_id are excluded — they retain implicit
    # uniqueness from the old constraint era and are not affected.
    # ------------------------------------------------------------------
    op.create_index(
        "uix_billing_document_company_kind_number",
        "billing_documents",
        ["company_id", "kind", "document_number"],
        unique=True,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Remove the new partial unique index
    # ------------------------------------------------------------------
    op.drop_index(
        "uix_billing_document_company_kind_number",
        table_name="billing_documents",
    )

    # ------------------------------------------------------------------
    # Restore the old per-user unique constraint
    # NOTE: downgrade may fail if duplicate (user_id, kind, document_number)
    # rows were created after the upgrade (multi-company feature was used).
    # Clean up duplicates before downgrading.
    # ------------------------------------------------------------------
    op.create_unique_constraint(
        "uq_billing_document_user_kind_number",
        "billing_documents",
        ["user_id", "kind", "document_number"],
    )
