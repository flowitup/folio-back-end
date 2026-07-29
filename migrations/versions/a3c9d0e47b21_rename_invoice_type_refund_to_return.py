"""Rename the invoicetype enum value 'refund' to 'return'.

The invoice type 'refund' (supplier credit note) collided in wording with the
unrelated refundable_status reimbursement flow. The canonical wire and stored
value becomes 'return'; the API keeps accepting 'refund' on input as a legacy
alias. Renaming the enum label migrates every stored row in place.

Revision ID: a3c9d0e47b21
Revises: f8b5833b149b
"""

from alembic import op

revision = "a3c9d0e47b21"
down_revision = "f8b5833b149b"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'refund' TO 'return'")


def downgrade():
    op.execute("ALTER TYPE invoicetype RENAME VALUE 'return' TO 'refund'")
