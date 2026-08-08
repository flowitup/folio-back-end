"""Add worker_id column to invoices, linking labor invoices to a worker.

Nullable FK to workers.id, ON DELETE SET NULL so deleting a worker does not
delete or block the historical invoice — it just drops the link. Only
settable when invoice type == 'labor' (enforced at the application layer,
not here).

Revision ID: 1291d5f4fa9f
Revises: 033b9c30eea0
Create Date: 2026-08-08 01:45:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "1291d5f4fa9f"
down_revision = "033b9c30eea0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_invoices_worker_id", "invoices", ["worker_id"])
    op.create_foreign_key(
        "fk_invoices_worker_id",
        "invoices",
        "workers",
        ["worker_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invoices_worker_id", "invoices", type_="foreignkey")
    op.drop_index("ix_invoices_worker_id", table_name="invoices")
    op.drop_column("invoices", "worker_id")
