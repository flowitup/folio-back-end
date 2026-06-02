"""notes_add_category_relax_reminders

Adds a category column to scope notes as journal entries (inspection, delivery,
payment, decision, call, general). Relaxes legacy reminder columns
(due_date, lead_time_minutes, status) to nullable so new journal rows do not
need them. Drops the reminder-specific CHECK constraints and reminder indexes
that are no longer valid once status/lead_time_minutes become nullable. Adds a
composite index on (project_id, created_at) for the journal list query.

Revision ID: a1b2c4d8f3e9
Revises: 3a1b2c4d5e6f
Create Date: 2026-06-02 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c4d8f3e9"
down_revision = "3a1b2c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add category column with server default so existing rows get 'general'.
    op.add_column(
        "notes",
        sa.Column("category", sa.String(20), nullable=False, server_default="general"),
    )
    op.create_check_constraint(
        "ck_notes_category",
        "notes",
        "category IN ('inspection','delivery','payment','decision','call','general')",
    )

    # 2. Add composite index supporting journal list query (project + created_at).
    op.create_index("ix_notes_project_created", "notes", ["project_id", "created_at"])

    # 3. Drop CHECK constraints on legacy reminder fields so we can make them nullable.
    #    Constraint names were set in the original notes migration (dfe858ecff3d).
    #    Hand-written drops — autogenerate cannot reliably detect CHECK drops.
    op.drop_constraint("ck_notes_lead_time_minutes", "notes", type_="check")
    op.drop_constraint("ck_notes_status", "notes", type_="check")

    # 4. Alter legacy reminder columns to nullable — new journal rows will leave
    #    them NULL; legacy rows retain their existing values unchanged.
    op.alter_column("notes", "due_date", existing_type=sa.Date(), nullable=True)
    op.alter_column("notes", "lead_time_minutes", existing_type=sa.Integer(), nullable=True)
    op.alter_column("notes", "status", existing_type=sa.String(16), nullable=True)

    # 5. Drop reminder-specific indexes (no longer valid for mixed nullable data).
    #    ix_notes_project_id was already dropped in migration f1a2b3c4d5e6.
    op.drop_index("ix_notes_due_date", table_name="notes")
    op.drop_index("ix_notes_project_status_due", table_name="notes")


def downgrade() -> None:
    # Restore reminder indexes first (need column to be NOT NULL for the status index).
    op.create_index("ix_notes_project_status_due", "notes", ["project_id", "status", "due_date"])
    op.create_index("ix_notes_due_date", "notes", ["due_date"])

    # Restore NOT NULL on reminder columns.
    # Note: if any NULL values exist this will fail — acceptable, migration is
    # forward-only in production (downgrade provided for completeness).
    op.alter_column("notes", "status", existing_type=sa.String(16), nullable=False)
    op.alter_column("notes", "lead_time_minutes", existing_type=sa.Integer(), nullable=False)
    op.alter_column("notes", "due_date", existing_type=sa.Date(), nullable=False)

    # Restore CHECK constraints on reminder fields.
    op.create_check_constraint(
        "ck_notes_status",
        "notes",
        "status IN ('open','done')",
    )
    op.create_check_constraint(
        "ck_notes_lead_time_minutes",
        "notes",
        "lead_time_minutes IN (0,60,1440)",
    )

    # Drop journal index and category column.
    op.drop_index("ix_notes_project_created", table_name="notes")
    op.drop_constraint("ck_notes_category", "notes", type_="check")
    op.drop_column("notes", "category")
