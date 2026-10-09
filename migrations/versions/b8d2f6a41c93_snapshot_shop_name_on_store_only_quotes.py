"""Give every chiffrage price recorded only at a shop the shop's name.

A price saved with a shop and no supplier name (the mobile app sends this when
the optional name is left blank) broke the supplier check constraint the
moment its shop was detached: deleting the shop, or the whole project (the
store FK is ON DELETE SET NULL), failed. New prices now snapshot the shop's
name; this backfills the existing ones. Data only: no schema change.

Revision ID: b8d2f6a41c93
Revises: c6e1a9f4b7d2
Create Date: 2026-10-09
"""

from alembic import op

revision = "b8d2f6a41c93"
down_revision = "c6e1a9f4b7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE chiffrage_quotes AS q
           SET supplier_name = left(s.name, 120)
          FROM chiffrage_stores AS s
         WHERE q.store_id = s.id
           AND (q.supplier_name IS NULL OR btrim(q.supplier_name) = '')
        """
    )


def downgrade() -> None:
    # The snapshot is harmless to keep and cannot be told apart from a typed name.
    pass
