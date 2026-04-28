"""labor supplement hours: add supplement_hours column, make shift_type nullable, add check constraints

Revision ID: 20a22df3582d
Revises: f1a2b3c4d5e6
Create Date: 2026-04-28 12:08:00.000000

DOWNGRADE WARNING
-----------------
downgrade() is DESTRUCTIVE. It will DELETE any rows in labor_entries where
shift_type IS NULL (i.e., supplement-only entries that have no shift_type set).
These rows cannot be recovered after downgrade without a prior backup.

Recommended: take a full DB backup before running `flask db downgrade -1`.
"""

from alembic import op
import sqlalchemy as sa

revision = "20a22df3582d"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add supplement_hours with a server_default so existing rows get 0
    op.add_column(
        "labor_entries",
        sa.Column("supplement_hours", sa.Integer(), nullable=False, server_default="0"),
    )

    # 2. Drop the server_default — application layer and DB entity default=0 are sufficient
    op.alter_column("labor_entries", "supplement_hours", server_default=None)

    # 3. Make shift_type nullable (supplement-only rows have no shift_type)
    op.alter_column(
        "labor_entries",
        "shift_type",
        existing_type=sa.String(length=20),
        nullable=True,
    )

    # 4. CHECK: supplement_hours must be in [0, 12]
    op.create_check_constraint(
        "chk_labor_supplement_hours_range",
        "labor_entries",
        "supplement_hours >= 0 AND supplement_hours <= 12",
    )

    # 5. CHECK: at least one of shift_type or supplement_hours > 0 must be set
    op.create_check_constraint(
        "chk_labor_entry_nonempty",
        "labor_entries",
        "shift_type IS NOT NULL OR supplement_hours > 0",
    )


def downgrade():
    # DESTRUCTIVE — deletes supplement-only rows (shift_type IS NULL) before
    # restoring NOT NULL on shift_type. See module docstring.
    op.execute("DELETE FROM labor_entries WHERE shift_type IS NULL")

    op.drop_constraint("chk_labor_entry_nonempty", "labor_entries", type_="check")
    op.drop_constraint("chk_labor_supplement_hours_range", "labor_entries", type_="check")

    op.alter_column(
        "labor_entries",
        "shift_type",
        existing_type=sa.String(length=20),
        nullable=False,
    )

    op.drop_column("labor_entries", "supplement_hours")
