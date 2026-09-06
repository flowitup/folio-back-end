"""Drop project tags (phase tags).

Removes the project_tags table and the tag_id columns on labor_entries and invoices.
Existing tag assignments are dropped with the columns; the downgrade recreates the
schema empty. Free-text tags on project documents / analyses are unrelated and untouched.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_invoices_tag_id", "invoices", type_="foreignkey")
    op.drop_index("ix_invoices_tag_id", table_name="invoices")
    op.drop_column("invoices", "tag_id")

    op.drop_constraint("fk_labor_entries_tag_id", "labor_entries", type_="foreignkey")
    op.drop_index("ix_labor_entries_tag_id", table_name="labor_entries")
    op.drop_column("labor_entries", "tag_id")

    op.drop_table("project_tags")


def downgrade() -> None:
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

    op.add_column("labor_entries", sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_labor_entries_tag_id", "labor_entries", ["tag_id"])
    op.create_foreign_key(
        "fk_labor_entries_tag_id",
        "labor_entries",
        "project_tags",
        ["tag_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("invoices", sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_invoices_tag_id", "invoices", ["tag_id"])
    op.create_foreign_key(
        "fk_invoices_tag_id",
        "invoices",
        "project_tags",
        ["tag_id"],
        ["id"],
        ondelete="SET NULL",
    )
