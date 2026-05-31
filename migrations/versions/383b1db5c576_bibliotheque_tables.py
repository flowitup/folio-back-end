"""bibliotheque_tables

Revision ID: 383b1db5c576
Revises: fe343de24e08
Create Date: 2026-05-31 04:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "383b1db5c576"
down_revision = "e2f162261785"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------ #
    # bibliotheque_suppliers                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "bibliotheque_suppliers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("website_url", sa.String(500), nullable=True),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("product_url_template", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_unique_constraint(
        "uq_bibliotheque_suppliers_company_slug",
        "bibliotheque_suppliers",
        ["company_id", "slug"],
    )
    op.create_index(
        "ix_bibliotheque_suppliers_company_id",
        "bibliotheque_suppliers",
        ["company_id"],
    )

    # ------------------------------------------------------------------ #
    # bibliotheque_products                                                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "bibliotheque_products",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("bibliotheque_suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("supplier_reference", sa.String(255), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("size", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        # Nullable unique: PostgreSQL permits multiple NULLs in a unique column.
        sa.Column("image_storage_key", sa.String(500), nullable=True, unique=True),
        sa.Column("product_url", sa.String(500), nullable=True),
        sa.Column("purchase_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "total_quantity",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_unit_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("first_purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_unique_constraint(
        "uq_bibliotheque_products_company_supplier_ref",
        "bibliotheque_products",
        ["company_id", "supplier_id", "supplier_reference"],
    )
    op.create_index(
        "ix_bibliotheque_products_company_supplier",
        "bibliotheque_products",
        ["company_id", "supplier_id"],
    )
    op.create_index(
        "ix_bibliotheque_products_company_category",
        "bibliotheque_products",
        ["company_id", "category"],
    )

    # ------------------------------------------------------------------ #
    # bibliotheque_purchases                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "bibliotheque_purchases",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("bibliotheque_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_document_ref", sa.String(255), nullable=False),
        sa.Column("source_document_type", sa.String(20), nullable=False),
        sa.Column("line_index", sa.Integer, nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_unique_constraint(
        "uq_bibliotheque_purchases_idempotency_key",
        "bibliotheque_purchases",
        ["product_id", "source_document_ref", "line_index"],
    )
    op.create_index(
        "ix_bibliotheque_purchases_product_purchased_at",
        "bibliotheque_purchases",
        ["product_id", "purchased_at"],
    )

    # ------------------------------------------------------------------ #
    # Seed bibliotheque:manage permission (additive — skip if present)    #
    # ------------------------------------------------------------------ #
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO permissions (id, name, resource, action, created_at)
            VALUES (gen_random_uuid(), 'bibliotheque:manage', 'bibliotheque', 'manage', NOW())
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade():
    # Drop in FK-safe order: purchases → products → suppliers
    op.drop_index("ix_bibliotheque_purchases_product_purchased_at", "bibliotheque_purchases")
    op.drop_constraint("uq_bibliotheque_purchases_idempotency_key", "bibliotheque_purchases", type_="unique")
    op.drop_table("bibliotheque_purchases")

    op.drop_index("ix_bibliotheque_products_company_category", "bibliotheque_products")
    op.drop_index("ix_bibliotheque_products_company_supplier", "bibliotheque_products")
    op.drop_constraint("uq_bibliotheque_products_company_supplier_ref", "bibliotheque_products", type_="unique")
    op.drop_table("bibliotheque_products")

    op.drop_index("ix_bibliotheque_suppliers_company_id", "bibliotheque_suppliers")
    op.drop_constraint("uq_bibliotheque_suppliers_company_slug", "bibliotheque_suppliers", type_="unique")
    op.drop_table("bibliotheque_suppliers")

    # Remove the seeded permission (best-effort — FK cascade handles role_permissions)
    op.get_bind().execute(sa.text("DELETE FROM permissions WHERE name = 'bibliotheque:manage'"))
