"""Scope chiffrage stores to the project instead of the poste.

A shop attached to a poste is a different row in every section, so "Leroy
Merlin" in Plomberie and in Électricité could never be totalled together — and
totalling a shop's basket across the chantier is the point of the comparison.

Duplicates created under the old scoping are merged by lower(trim(name)) within
a project, keeping the earliest row. The losing rows' addresses are printed
before deletion rather than dropped silently, so a genuinely different branch
recorded under an identical name can be re-entered by hand afterwards.

Downgrade is lossy by nature: a project-level shop has no single poste to
return to, so each is re-attached to the project's first poste by position.

Revision ID: d3f9a1c85b27
Revises: c7e1a94db302
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3f9a1c85b27"
down_revision = "c7e1a94db302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("chiffrage_stores", sa.Column("project_id", sa.UUID(), nullable=True))

    # Walk poste -> project for every existing row.
    bind.execute(
        sa.text(
            """
            UPDATE chiffrage_stores s
               SET project_id = p.project_id
              FROM chiffrage_postes p
             WHERE p.id = s.poste_id
            """
        )
    )

    # Report what the merge is about to fold together, then fold it.
    doomed = bind.execute(
        sa.text(
            """
            SELECT s.id, s.name, s.address
              FROM chiffrage_stores s
             WHERE EXISTS (
                   SELECT 1 FROM chiffrage_stores k
                    WHERE k.project_id = s.project_id
                      AND lower(btrim(k.name)) = lower(btrim(s.name))
                      AND (k.created_at, k.id) < (s.created_at, s.id)
             )
            """
        )
    ).fetchall()
    for row in doomed:
        print(f"[scope_chiffrage_stores] merging duplicate shop '{row.name}' (address={row.address!r})")

    bind.execute(
        sa.text(
            """
            DELETE FROM chiffrage_stores s
             WHERE EXISTS (
                   SELECT 1 FROM chiffrage_stores k
                    WHERE k.project_id = s.project_id
                      AND lower(btrim(k.name)) = lower(btrim(s.name))
                      AND (k.created_at, k.id) < (s.created_at, s.id)
             )
            """
        )
    )

    op.alter_column("chiffrage_stores", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_chiffrage_stores_project_id",
        "chiffrage_stores",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("ix_chiffrage_stores_poste_position", table_name="chiffrage_stores")
    op.drop_column("chiffrage_stores", "poste_id")

    op.create_index(
        "ix_chiffrage_stores_project_position",
        "chiffrage_stores",
        ["project_id", "position"],
    )
    op.create_index(
        "uq_chiffrage_stores_project_name",
        "chiffrage_stores",
        ["project_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.add_column("chiffrage_stores", sa.Column("poste_id", sa.UUID(), nullable=True))

    # Lossy: the original poste is not recoverable, so every shop lands on the
    # project's first poste by position.
    bind.execute(
        sa.text(
            """
            UPDATE chiffrage_stores s
               SET poste_id = (
                   SELECT p.id FROM chiffrage_postes p
                    WHERE p.project_id = s.project_id
                    ORDER BY p.position, p.created_at
                    LIMIT 1
               )
            """
        )
    )
    # A project with no poste at all has nowhere to put its shops.
    bind.execute(sa.text("DELETE FROM chiffrage_stores WHERE poste_id IS NULL"))

    op.drop_index("uq_chiffrage_stores_project_name", table_name="chiffrage_stores")
    op.drop_index("ix_chiffrage_stores_project_position", table_name="chiffrage_stores")

    op.alter_column("chiffrage_stores", "poste_id", nullable=False)
    op.create_foreign_key(
        "fk_chiffrage_stores_poste_id",
        "chiffrage_stores",
        "chiffrage_postes",
        ["poste_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_chiffrage_stores_poste_position",
        "chiffrage_stores",
        ["poste_id", "position"],
    )

    op.drop_constraint("fk_chiffrage_stores_project_id", "chiffrage_stores", type_="foreignkey")
    op.drop_column("chiffrage_stores", "project_id")
