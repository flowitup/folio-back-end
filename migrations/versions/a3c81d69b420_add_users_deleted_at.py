"""add_users_deleted_at

Audit column for self-service account deletion (App Store guideline 5.1.1(v)).

The row is never removed: 36 foreign keys point at users.id, several of them
RESTRICT/NOT NULL, and billing_documents/chat_messages CASCADE — so a real
DELETE would either fail or destroy company data. Deletion anonymizes the row
instead, and this column records when, which an overwritable updated_at cannot.

Revision ID: a3c81d69b420
Revises: 4175995468e3
Create Date: 2026-09-15

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a3c81d69b420"
down_revision = "4175995468e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
