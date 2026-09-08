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

The two steps that READ the legacy role tables live in this file rather than in
the shared package: a later revision drops those tables, so the queries below
are only ever valid while upgrading through this revision.

Revision ID: 9a4c1e7b2d05
Revises: 7d3e9a1b4c5f
Create Date: 2026-09-08 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import (
    BackfillReport,
    access_rows,
    company_label,
    raise_role,
    user_label,
)
from app.infrastructure.database.backfills.creator_assignments import backfill_creator_assignments
from app.infrastructure.database.backfills.directory_profiles import backfill_directory_profiles

_OPS_USERS_SQL = text(
    """
    SELECT DISTINCT ur.user_id
    FROM user_roles ur
    JOIN role_permissions rp ON rp.role_id = ur.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE p.name = '*:*'
    """
)

_GLOBAL_ROLE_USERS_SQL = text(
    """
    SELECT DISTINCT ur.user_id
    FROM user_roles ur
    JOIN roles r ON r.id = ur.role_id
    WHERE r.name = :role_name
    """
)

_SET_OPS_SQL = text("UPDATE users SET is_platform_ops = TRUE WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)")

_ANY_ROLE_ID_SQL = text("SELECT id FROM roles WHERE name = :role_name")

_INSERT_ASSIGNMENT_SQL = text(
    """
    INSERT INTO user_projects (user_id, project_id, role_id, invited_by_user_id, assigned_at)
    VALUES (:user_id, :project_id, :role_id, NULL, :assigned_at)
    """
)


def _insert_assignment_with_legacy_role(conn: Connection, user_id, project_id, assigned_at) -> None:
    """Write a `user_projects` row while `role_id` is still NOT NULL.

    Any legacy role satisfies the constraint — the column grants nothing by
    this revision and a later one drops it.
    """
    role_id = None
    for role_name in ("manager", "admin", "member"):
        row = conn.execute(_ANY_ROLE_ID_SQL, {"role_name": role_name}).fetchone()
        if row is not None:
            role_id = row[0]
            break
    if role_id is None:
        raise RuntimeError("no legacy role row to satisfy user_projects.role_id — is the database seeded?")
    conn.execute(
        _INSERT_ASSIGNMENT_SQL,
        {"user_id": user_id, "project_id": project_id, "role_id": role_id, "assigned_at": assigned_at},
    )


def backfill_platform_ops(conn: Connection, report: BackfillReport) -> None:
    """Step 1: legacy `*:*` holders → ops flag + admin of their primary/sole company."""
    for (user_id,) in conn.execute(_OPS_USERS_SQL).fetchall():
        conn.execute(_SET_OPS_SQL, {"user_id": str(user_id)})
        report.ops_users += 1
        rows = access_rows(conn, user_id)
        if len(rows) == 1:
            target = rows[0]
        else:
            target = next((r for r in rows if r[2]), None)
        if target is None:
            report.lines.append(f"  ops: {user_label(conn, user_id)} (no company attachment — flag only)")
            continue
        company_id, role = target[0], target[1]
        if raise_role(conn, user_id, company_id, "admin", role):
            report.ops_company_admins += 1
        report.lines.append(f"  ops: {user_label(conn, user_id)} → admin of {company_label(conn, company_id)}")


def backfill_global_managers(conn: Connection, report: BackfillReport) -> None:
    """Step 2: legacy global `manager` role → company `manager` wherever attached."""
    for (user_id,) in conn.execute(_GLOBAL_ROLE_USERS_SQL, {"role_name": "manager"}).fetchall():
        for company_id, role, _is_primary in access_rows(conn, user_id):
            if raise_role(conn, user_id, company_id, "manager", role):
                report.global_managers += 1
                report.lines.append(
                    f"  manager: {user_label(conn, user_id)} → manager of {company_label(conn, company_id)}"
                )


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
