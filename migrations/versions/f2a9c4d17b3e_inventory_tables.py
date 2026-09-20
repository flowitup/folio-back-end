"""inventory_tables

Company equipment inventory: the tools and machines a company owns, how many,
whether each is working or damaged, and where it is — one of the company's
warehouses (with its address) or a site (a project).

Revision ID: f2a9c4d17b3e
Revises: a3c81d69b420
Create Date: 2026-09-20

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f2a9c4d17b3e"
down_revision = "a3c81d69b420"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_warehouses",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_inventory_warehouses_company_id", "inventory_warehouses", ["company_id"])

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("location_type", sa.String(20), nullable=False),
        # A warehouse in use cannot be dropped under its rows (the API answers 409 first).
        sa.Column(
            "warehouse_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("inventory_warehouses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # A deleted project leaves its rows as "unknown location" instead of blocking the delete.
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_items_quantity_non_negative"),
        sa.CheckConstraint("condition IN ('working', 'damaged')", name="ck_inventory_items_condition"),
        sa.CheckConstraint("location_type IN ('warehouse', 'site')", name="ck_inventory_items_location_type"),
    )
    op.create_index("ix_inventory_items_company_id", "inventory_items", ["company_id"])
    op.create_index("ix_inventory_items_warehouse_id", "inventory_items", ["warehouse_id"])
    op.create_index("ix_inventory_items_project_id", "inventory_items", ["project_id"])

    # Seed the inventory:manage permission (additive — skip if present). The
    # role matrix lives in code (app.domain.authz.matrix); this row exists for
    # the permissions catalog and D8 grant/deny rows.
    op.execute(
        sa.text(
            """
            INSERT INTO permissions (id, name, resource, action, created_at)
            VALUES (gen_random_uuid(), 'inventory:manage', 'inventory', 'manage', NOW())
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade():
    op.drop_index("ix_inventory_items_project_id", "inventory_items")
    op.drop_index("ix_inventory_items_warehouse_id", "inventory_items")
    op.drop_index("ix_inventory_items_company_id", "inventory_items")
    op.drop_table("inventory_items")

    op.drop_index("ix_inventory_warehouses_company_id", "inventory_warehouses")
    op.drop_table("inventory_warehouses")

    op.execute(sa.text("DELETE FROM permissions WHERE name = 'inventory:manage'"))
