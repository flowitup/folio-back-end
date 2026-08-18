"""add_website_to_chiffrage_stores

A shop's website, alongside its name and address.

Revision ID: a91c4e7d2b58
Revises: e5b2c74a19f3
Create Date: 2026-08-18 23:58:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a91c4e7d2b58"
down_revision = "e5b2c74a19f3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chiffrage_stores", sa.Column("website_url", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("chiffrage_stores", "website_url")
