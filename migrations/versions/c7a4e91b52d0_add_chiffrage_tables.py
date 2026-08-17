"""add_chiffrage_tables

Per-project material provisioning: postes -> articles -> supplier quotes,
plus project-scoped custom units of measure.

Revision ID: c7a4e91b52d0
Revises: 9963d6733330
Create Date: 2026-08-18 01:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7a4e91b52d0"
down_revision = "9963d6733330"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------ #
    # chiffrage_postes — a costing section, e.g. "Lumière"                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chiffrage_postes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_chiffrage_postes_project_position", "chiffrage_postes", ["project_id", "position"])

    # ------------------------------------------------------------------ #
    # chiffrage_articles — one thing to buy inside a poste                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chiffrage_articles",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "poste_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
        # Snapshot symbol, deliberately not a FK to chiffrage_units: deleting a
        # custom unit must never break articles that already reference it.
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_chiffrage_articles_poste_position", "chiffrage_articles", ["poste_id", "position"])

    # ------------------------------------------------------------------ #
    # chiffrage_quotes — one fournisseur offer for an article             #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chiffrage_quotes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "article_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("chiffrage_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL (not CASCADE): removing a supplier or product from the
        # bibliothèque must not delete costing history. supplier_name keeps the
        # row readable afterwards.
        sa.Column(
            "supplier_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("bibliotheque_suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("supplier_name", sa.String(120), nullable=True),
        sa.Column(
            "library_product_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("bibliotheque_products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Canonical HT + rate; TTC is always derived, never stored.
        sa.Column("unit_price_ht", sa.Numeric(12, 4), nullable=False),
        sa.Column("tva_rate", sa.Numeric(5, 2), nullable=False, server_default=sa.text("20")),
        sa.Column("product_url", sa.String(500), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "supplier_id IS NOT NULL OR supplier_name IS NOT NULL",
            name="ck_chiffrage_quotes_supplier_present",
        ),
    )
    op.create_index("ix_chiffrage_quotes_article_selected", "chiffrage_quotes", ["article_id", "is_selected"])

    # ------------------------------------------------------------------ #
    # chiffrage_units — custom units on top of the preset constant        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "chiffrage_units",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "symbol", name="uq_chiffrage_units_project_symbol"),
    )
    op.create_index("ix_chiffrage_units_project_id", "chiffrage_units", ["project_id"])


def downgrade():
    op.drop_index("ix_chiffrage_units_project_id", table_name="chiffrage_units")
    op.drop_table("chiffrage_units")
    op.drop_index("ix_chiffrage_quotes_article_selected", table_name="chiffrage_quotes")
    op.drop_table("chiffrage_quotes")
    op.drop_index("ix_chiffrage_articles_poste_position", table_name="chiffrage_articles")
    op.drop_table("chiffrage_articles")
    op.drop_index("ix_chiffrage_postes_project_position", table_name="chiffrage_postes")
    op.drop_table("chiffrage_postes")
