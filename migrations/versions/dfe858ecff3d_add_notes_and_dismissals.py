"""add_notes_and_dismissals

Revision ID: dfe858ecff3d
Revises: e3f1a2b4c5d6
Create Date: 2026-04-27 18:27:02.989630

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "dfe858ecff3d"
down_revision = "e3f1a2b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "lead_time_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="open",
        ),
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
        sa.CheckConstraint(
            "lead_time_minutes IN (0, 60, 1440)",
            name="ck_notes_lead_time_minutes",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done')",
            name="ck_notes_status",
        ),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 200",
            name="ck_notes_title_length",
        ),
    )
    op.create_index("ix_notes_project_id", "notes", ["project_id"])
    op.create_index("ix_notes_due_date", "notes", ["due_date"])
    op.create_index(
        "ix_notes_project_status_due",
        "notes",
        ["project_id", "status", "due_date"],
    )

    op.create_table(
        "notes_dismissed",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "note_id",
            sa.UUID(),
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_notes_dismissed_user_id", "notes_dismissed", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notes_dismissed_user_id", table_name="notes_dismissed")
    op.drop_table("notes_dismissed")
    op.drop_index("ix_notes_project_status_due", table_name="notes")
    op.drop_index("ix_notes_due_date", table_name="notes")
    op.drop_index("ix_notes_project_id", table_name="notes")
    op.drop_table("notes")
