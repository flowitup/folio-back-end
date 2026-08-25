"""Link each chiffrage quote to the shop the price came from.

Until now a price carried a free-typed supplier_name, so two spellings of one
shop were two shops and no basket could be totalled. This adds the shop link
and, critically, backfills it: every existing price is matched to its project's
shops by name, and any unmatched non-blank name becomes a shop. Without that
backfill all historical prices would fall out of the comparison the moment it
shipped.

supplier_name is deliberately kept as a readable snapshot, so removing a shop
(ON DELETE SET NULL) never blanks out the costing row.

Revision ID: e4a7c26d91f8
Revises: d3f9a1c85b27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4a7c26d91f8"
down_revision = "d3f9a1c85b27"
branch_labels = None
depends_on = None

_OLD_SUPPLIER_CHECK = "supplier_id IS NOT NULL OR supplier_name IS NOT NULL"
_NEW_SUPPLIER_CHECK = "store_id IS NOT NULL OR supplier_id IS NOT NULL OR supplier_name IS NOT NULL"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("chiffrage_quotes", sa.Column("store_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_chiffrage_quotes_store_id",
        "chiffrage_quotes",
        "chiffrage_stores",
        ["store_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chiffrage_quotes_store_id", "chiffrage_quotes", ["store_id"])

    # 1. Create a shop for every supplier name that has no matching shop yet.
    #    position continues the project's existing numbering.
    created = bind.execute(
        sa.text(
            """
            INSERT INTO chiffrage_stores (id, project_id, name, address, website_url, position,
                                          created_at, updated_at)
            SELECT gen_random_uuid(),
                   src.project_id,
                   src.name,
                   NULL,
                   NULL,
                   1000 * ROW_NUMBER() OVER (PARTITION BY src.project_id ORDER BY src.name)
                        + COALESCE((SELECT max(position) FROM chiffrage_stores x
                                     WHERE x.project_id = src.project_id), 0),
                   now(),
                   now()
              FROM (
                   SELECT DISTINCT p.project_id, btrim(q.supplier_name) AS name
                     FROM chiffrage_quotes q
                     JOIN chiffrage_articles a ON a.id = q.article_id
                     JOIN chiffrage_postes p ON p.id = a.poste_id
                    WHERE q.supplier_name IS NOT NULL
                      AND btrim(q.supplier_name) <> ''
              ) src
             WHERE NOT EXISTS (
                   SELECT 1 FROM chiffrage_stores s
                    WHERE s.project_id = src.project_id
                      AND lower(btrim(s.name)) = lower(src.name)
             )
            RETURNING name
            """
        )
    ).fetchall()
    for row in created:
        print(f"[link_quotes_to_shop] created shop '{row.name}' from an existing price")

    # 2. Point every quote at the matching shop of its own project.
    linked = bind.execute(
        sa.text(
            """
            UPDATE chiffrage_quotes q
               SET store_id = s.id
              FROM chiffrage_articles a
              JOIN chiffrage_postes p ON p.id = a.poste_id
              JOIN chiffrage_stores s ON s.project_id = p.project_id
             WHERE a.id = q.article_id
               AND q.supplier_name IS NOT NULL
               AND lower(btrim(q.supplier_name)) = lower(btrim(s.name))
            """
        )
    )
    print(f"[link_quotes_to_shop] linked {linked.rowcount} price(s) to a shop")

    op.drop_constraint("ck_chiffrage_quotes_supplier_present", "chiffrage_quotes", type_="check")
    op.create_check_constraint("ck_chiffrage_quotes_supplier_present", "chiffrage_quotes", _NEW_SUPPLIER_CHECK)


def downgrade() -> None:
    # Rows attributed only by shop would violate the old constraint, so the
    # shop name is written back into supplier_name before the link is dropped.
    op.get_bind().execute(
        sa.text(
            """
            UPDATE chiffrage_quotes q
               SET supplier_name = s.name
              FROM chiffrage_stores s
             WHERE s.id = q.store_id
               AND (q.supplier_name IS NULL OR btrim(q.supplier_name) = '')
            """
        )
    )

    op.drop_constraint("ck_chiffrage_quotes_supplier_present", "chiffrage_quotes", type_="check")
    op.create_check_constraint("ck_chiffrage_quotes_supplier_present", "chiffrage_quotes", _OLD_SUPPLIER_CHECK)

    op.drop_index("ix_chiffrage_quotes_store_id", table_name="chiffrage_quotes")
    op.drop_constraint("fk_chiffrage_quotes_store_id", "chiffrage_quotes", type_="foreignkey")
    op.drop_column("chiffrage_quotes", "store_id")
