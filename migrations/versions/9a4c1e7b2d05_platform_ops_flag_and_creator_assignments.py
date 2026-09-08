"""platform ops flag, creator assignments and company role mapping

Additive: adds `users.is_platform_ops` and rewrites relationships (company
roles, project assignments, directory profiles) that the permission resolver
reads. No table or column is dropped, so redeploying the previous image rolls
this release back.

Data steps live in
app.infrastructure.database.backfills.platform_ops_and_creator_assignments
(shared with the migration test) and print their mapping so it can be reviewed
against a prod dump.

Revision ID: 9a4c1e7b2d05
Revises: 7d3e9a1b4c5f
Create Date: 2026-09-08 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import run_backfill

# revision identifiers, used by Alembic.
revision = "9a4c1e7b2d05"
down_revision = "7d3e9a1b4c5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_platform_ops", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    report = run_backfill(op.get_bind())
    print(report.summary())
    for line in report.lines:
        print(line)


def downgrade() -> None:
    # Only the column is reversible: the role/assignment/profile rows the data
    # step created are indistinguishable from ones an admin created afterwards,
    # so removing them would destroy real access.
    op.drop_column("users", "is_platform_ops")
