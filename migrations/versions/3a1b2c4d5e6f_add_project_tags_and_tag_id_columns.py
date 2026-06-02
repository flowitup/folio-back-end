"""add project_tags table and tag_id FK columns on labor_entries and invoices

Revision ID: 3a1b2c4d5e6f
Revises: 383b1db5c576
Create Date: 2026-06-02 00:58:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "3a1b2c4d5e6f"
down_revision = "383b1db5c576"
branch_labels = None
depends_on = None


def upgrade():
    # --- project_tags table -----------------------------------------------
    op.create_table(
        "project_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE", name="fk_project_tags_project_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "name", name="uq_project_tags_project_id_name"),
    )

    # --- labor_entries.tag_id ---------------------------------------------
    op.add_column(
        "labor_entries",
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_labor_entries_tag_id", "labor_entries", ["tag_id"])
    op.create_foreign_key(
        "fk_labor_entries_tag_id",
        "labor_entries",
        "project_tags",
        ["tag_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- invoices.tag_id --------------------------------------------------
    op.add_column(
        "invoices",
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_invoices_tag_id", "invoices", ["tag_id"])
    op.create_foreign_key(
        "fk_invoices_tag_id",
        "invoices",
        "project_tags",
        ["tag_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    # --- invoices.tag_id --------------------------------------------------
    op.drop_constraint("fk_invoices_tag_id", "invoices", type_="foreignkey")
    op.drop_index("ix_invoices_tag_id", table_name="invoices")
    op.drop_column("invoices", "tag_id")

    # --- labor_entries.tag_id ---------------------------------------------
    op.drop_constraint("fk_labor_entries_tag_id", "labor_entries", type_="foreignkey")
    op.drop_index("ix_labor_entries_tag_id", table_name="labor_entries")
    op.drop_column("labor_entries", "tag_id")

    # --- project_tags table -----------------------------------------------
    op.drop_table("project_tags")
