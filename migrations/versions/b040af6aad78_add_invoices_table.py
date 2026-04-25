"""add_invoices_table

Revision ID: b040af6aad78
Revises: a1b2c3d4e5f6
Create Date: 2026-04-21 22:35:35.340158

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b040af6aad78"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("invoice_number", sa.String(length=20), nullable=False),
        sa.Column("type", sa.Enum("client", "labor", "supplier", name="invoicetype"), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("recipient_name", sa.String(length=255), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "invoice_number", name="uq_project_invoice_number"),
    )
    op.create_index("ix_invoices_project_id", "invoices", ["project_id"], unique=False)
    op.create_index("ix_invoices_type", "invoices", ["type"], unique=False)


def downgrade():
    op.drop_index("ix_invoices_type", table_name="invoices")
    op.drop_index("ix_invoices_project_id", table_name="invoices")
    op.drop_table("invoices")
    sa.Enum(name="invoicetype").drop(op.get_bind(), checkfirst=True)
