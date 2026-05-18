"""add funds release link columns to invoices

Revision ID: a4f5b6c7d8e9
Revises: f2a3b4c5d6e7
Create Date: 2026-05-18 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "a4f5b6c7d8e9"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "invoices",
        sa.Column(
            "source_billing_document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing_documents.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
    )
    op.add_column(
        "invoices",
        sa.Column(
            "is_auto_generated",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade():
    op.drop_column("invoices", "is_auto_generated")
    op.drop_column("invoices", "source_billing_document_id")
