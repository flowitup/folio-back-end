"""add project_document_tags table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-05-23 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_document_tags",
        sa.Column(
            "document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "tag"),
    )
    op.create_index(
        "ix_project_document_tags_tag",
        "project_document_tags",
        ["tag"],
    )


def downgrade():
    op.drop_index("ix_project_document_tags_tag", table_name="project_document_tags")
    op.drop_table("project_document_tags")
