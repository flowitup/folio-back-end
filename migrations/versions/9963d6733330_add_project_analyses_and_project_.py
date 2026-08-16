"""add project_analyses and project_analysis_tags

Revision ID: 9963d6733330
Revises: 1291d5f4fa9f
Create Date: 2026-08-16 14:14:32.163223

Adds the project_analyses table (self-contained HTML report metadata; the
report body lives in object storage under storage_key) and the
project_analysis_tags composite-PK tag table. Soft-deletion is implemented
via the deleted_at nullable timestamp column. The partial index filters on
deleted_at IS NULL to keep active-analysis list scans fast — mirrors the
project_documents migration (818ba2f5ef63).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "9963d6733330"
down_revision = "1291d5f4fa9f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_analyses",
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
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="ck_project_analyses_size_bytes_nonneg"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_project_analyses_storage_key"),
    )
    op.create_index(
        "ix_project_analyses_project_created",
        "project_analyses",
        ["project_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "project_analysis_tags",
        sa.Column("analysis_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["project_analyses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("analysis_id", "tag"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_analyses_project_created",
        table_name="project_analyses",
    )
    op.drop_table("project_analysis_tags")
    op.drop_table("project_analyses")
