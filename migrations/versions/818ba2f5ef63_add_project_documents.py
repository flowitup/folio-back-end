"""add project_documents table

Revision ID: 818ba2f5ef63
Revises: f1a2b3c4d5e6
Create Date: 2026-05-16 15:00:00.000000

Adds the project_documents table for tracking project-scoped file uploads.
Soft-deletion is implemented via the deleted_at nullable timestamp column.
Partial indexes filter on deleted_at IS NULL to keep active-document scans fast.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "818ba2f5ef63"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_documents",
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="ck_project_documents_size_bytes_nonneg"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_project_documents_storage_key"),
    )
    op.create_index(
        "ix_project_documents_project_id_created_at",
        "project_documents",
        ["project_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_project_documents_uploader_user_id",
        "project_documents",
        ["uploader_user_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_documents_uploader_user_id",
        table_name="project_documents",
    )
    op.drop_index(
        "ix_project_documents_project_id_created_at",
        table_name="project_documents",
    )
    op.drop_table("project_documents")
