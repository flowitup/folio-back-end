"""drop_ix_notes_project_id

Revision ID: f1a2b3c4d5e6
Revises: dfe858ecff3d
Create Date: 2026-04-27 19:47:00.000000

Rationale (M6 code-review):
    The composite index ix_notes_project_status_due (project_id, status, due_date)
    is left-prefix on project_id, so the standalone ix_notes_project_id index is
    fully redundant. Dropping it reduces write amplification on INSERT/UPDATE/DELETE
    without losing any query-plan options.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "dfe858ecff3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_notes_project_id", table_name="notes")


def downgrade() -> None:
    op.create_index("ix_notes_project_id", "notes", ["project_id"])
