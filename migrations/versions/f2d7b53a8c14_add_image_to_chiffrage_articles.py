"""add_image_to_chiffrage_articles

Photo for each article. Bytes live in the object store; the row keeps the key.

Revision ID: f2d7b53a8c14
Revises: 4b8e2c6a91d7
Create Date: 2026-08-19 00:55:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f2d7b53a8c14"
down_revision = "a91c4e7d2b58"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chiffrage_articles", sa.Column("image_storage_key", sa.String(500), nullable=True))
    op.create_unique_constraint("uq_chiffrage_articles_image_storage_key", "chiffrage_articles", ["image_storage_key"])


def downgrade():
    op.drop_constraint("uq_chiffrage_articles_image_storage_key", "chiffrage_articles", type_="unique")
    op.drop_column("chiffrage_articles", "image_storage_key")
