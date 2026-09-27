"""invoice_number_counters: a persisted counter per project and number prefix

Expense and funds-release numbers (INV-YYYY-NNNN, FR-YYYY-NNNN) were the
highest number in use + 1. Deleting the latest expense freed its number for
the next one, and two concurrent creates read the same maximum, so one of
them failed with a 409 on uq_project_invoice_number. Numbers are now claimed
from this counter with one upsert, which serializes concurrent creates and
never hands a number out twice.

Seeded from the numbers already in use (the numeric suffix after the last
dash, compared as a number), so the next number of every existing sequence
is unchanged.

Revision ID: c3e7a91d5f20
Revises: b6d2f4a8c1e3
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c3e7a91d5f20"
down_revision = "b6d2f4a8c1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_number_counters",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "prefix", name="pk_invoice_number_counters"),
    )
    op.execute(
        """
        INSERT INTO invoice_number_counters (project_id, prefix, next_value)
        SELECT project_id,
               substring(invoice_number FROM '^(.*-)[0-9]+$') AS prefix,
               MAX(CAST(substring(invoice_number FROM '([0-9]+)$') AS BIGINT)) + 1
        FROM invoices
        WHERE invoice_number ~ '^.+-[0-9]+$'
        GROUP BY project_id, substring(invoice_number FROM '^(.*-)[0-9]+$')
        HAVING MAX(CAST(substring(invoice_number FROM '([0-9]+)$') AS BIGINT)) < 2147483647
        """
    )


def downgrade() -> None:
    # Numbers go back to "highest in use + 1"; the invoices keep their numbers.
    op.drop_table("invoice_number_counters")
