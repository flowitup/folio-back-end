"""create_labor_day_descriptions

One free-text description per (project_id, date) — separate from the
activity title (labor_activities) and per-worker entry notes.

Revision ID: ecbe5dd29ded
Revises: a9b8c7d6e5f4
Create Date: 2026-06-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ecbe5dd29ded"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labor_day_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("date", sa.Date(), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "date", name="uq_labor_day_descriptions_project_date"),
    )


def downgrade() -> None:
    op.drop_table("labor_day_descriptions")
