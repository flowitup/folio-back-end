"""add_purchase_tracking_to_chiffrage_articles

Shopping-list tick ("bought"), the quantity really bought and the quantity returned
for each article.

Revision ID: a7c3e91d5b24
Revises: e5a9c3d7f1b2
Create Date: 2026-10-26 09:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c3e91d5b24"
down_revision = "e5a9c3d7f1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chiffrage_articles", sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chiffrage_articles", sa.Column("purchased_quantity", sa.Numeric(12, 3), nullable=True))
    op.add_column("chiffrage_articles", sa.Column("returned_quantity", sa.Numeric(12, 3), nullable=True))


def downgrade():
    op.drop_column("chiffrage_articles", "returned_quantity")
    op.drop_column("chiffrage_articles", "purchased_quantity")
    op.drop_column("chiffrage_articles", "purchased_at")
