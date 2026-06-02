"""add project_photos table

Revision ID: c78cbcf5a73b
Revises: fe343de24e08, 383b1db5c576
Create Date: 2026-06-02 00:00:00.000000

Adds the project_photos table for tracking construction-progress photos
attached to a project. Soft-deletion is implemented via the deleted_at
nullable timestamp column. A partial index on (project_id, captured_at DESC,
created_at DESC) WHERE deleted_at IS NULL keeps active-photo list scans fast.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c78cbcf5a73b"
down_revision = ("fe343de24e08", "383b1db5c576")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_photos",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "uploader_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("thumbnail_storage_key", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="ck_project_photos_size_bytes_nonneg"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_project_photos_storage_key"),
    )
    # Partial index: active photos ordered by capture date then insertion date.
    # Covers the default list query (project_id filter + captured_at DESC sort).
    op.create_index(
        "ix_project_photos_project_captured",
        "project_photos",
        ["project_id", sa.text("captured_at DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_photos_project_captured",
        table_name="project_photos",
    )
    op.drop_table("project_photos")
