"""platform ops flag, creator assignments and company role mapping

Additive: adds `users.is_platform_ops` and rewrites relationships (company
roles, project assignments, directory profiles) that the permission resolver
reads. No table or column is dropped, so redeploying the previous image rolls
this release back.

Data steps live in
app.infrastructure.database.backfills.platform_ops_and_creator_assignments
(shared with the migration test) and print their mapping so it can be reviewed
against a prod dump. Users appear as a masked email plus their id — the mapping
lands in deploy logs.

The two steps that READ the legacy role tables live in
``scripts/migration_legacy_role_mapping.py`` rather than in ``app``: a later
revision drops those tables, so those queries are only ever valid while
upgrading through this revision. Keeping them in an importable module keeps
their unit tests (tests/unit/test_migration_legacy_role_mapping.py).

Revision ID: 9a4c1e7b2d05
Revises: 7d3e9a1b4c5f
Create Date: 2026-09-08 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport
from app.infrastructure.database.backfills.creator_assignments import backfill_creator_assignments
from app.infrastructure.database.backfills.directory_profiles import backfill_directory_profiles
from scripts.migration_legacy_role_mapping import backfill_global_managers, backfill_platform_ops

_ANY_ROLE_ID_SQL = text("SELECT id FROM roles WHERE name = :role_name")

_INSERT_ASSIGNMENT_SQL = text(
    """
    INSERT INTO user_projects (user_id, project_id, role_id, invited_by_user_id, assigned_at)
    VALUES (:user_id, :project_id, :role_id, NULL, :assigned_at)
    """
)


def _insert_assignment_with_legacy_role(conn: Connection, user_id, project_id, assigned_at) -> bool:
    """Write a `user_projects` row while `role_id` is still NOT NULL.

    Any legacy role satisfies the constraint — the column grants nothing by
    this revision and a later one drops it. An unseeded database has no role to
    point at: skip the row rather than fail the deploy (the count is reported).
    """
    role_id = None
    for role_name in ("manager", "admin", "member"):
        row = conn.execute(_ANY_ROLE_ID_SQL, {"role_name": role_name}).fetchone()
        if row is not None:
            role_id = row[0]
            break
    if role_id is None:
        return False
    conn.execute(
        _INSERT_ASSIGNMENT_SQL,
        {"user_id": user_id, "project_id": project_id, "role_id": role_id, "assigned_at": assigned_at},
    )
    return True


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

    bind = op.get_bind()
    report = BackfillReport()
    backfill_platform_ops(bind, report)
    backfill_global_managers(bind, report)
    backfill_creator_assignments(bind, report, insert_assignment=_insert_assignment_with_legacy_role)
    backfill_directory_profiles(bind, report)
    print(report.summary())
    for line in report.lines:
        print(line)
    # Never abort on these: migrations run at container start, so raising here
    # would be an outage. They need a human decision after the deploy.
    for warning in report.warnings:
        print(warning)


def downgrade() -> None:
    # Only the column is reversible: the role/assignment/profile rows the data
    # step created are indistinguishable from ones an admin created afterwards,
    # so removing them would destroy real access.
    op.drop_column("users", "is_platform_ops")
