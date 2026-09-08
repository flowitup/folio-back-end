"""Project creators keep their project after the owner bypass disappears (step 3).

Every project's ``owner_id`` gets a ``user_projects`` row (with the legacy
``manager`` ``role_id``, a column dropped in a later phase) and the owner's role
in the project's company is raised to at least ``manager``. An owner with no
access row for that company gets one — otherwise the person who created the
project would lose it at deploy time.

A project with no ``company_id`` cannot be attached to anyone: the resolver
answers "no permission" for it, so the step counts those rows and warns instead
of pretending the assignment it writes grants anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import (
    BackfillReport,
    access_rows,
    company_label,
    raise_role,
    user_label,
)

_ROLE_ID_SQL = text("SELECT id FROM roles WHERE name = :role_name")

_PROJECTS_SQL = text("SELECT id, owner_id, company_id FROM projects WHERE owner_id IS NOT NULL")

_PROJECTS_WITHOUT_COMPANY_SQL = text("SELECT COUNT(*) FROM projects WHERE company_id IS NULL")

_ASSIGNMENT_EXISTS_SQL = text(
    """
    SELECT 1 FROM user_projects
    WHERE CAST(user_id AS TEXT) = CAST(:user_id AS TEXT)
      AND CAST(project_id AS TEXT) = CAST(:project_id AS TEXT)
    """
)

_INSERT_ASSIGNMENT_SQL = text(
    """
    INSERT INTO user_projects (user_id, project_id, role_id, invited_by_user_id, assigned_at)
    VALUES (:user_id, :project_id, :role_id, NULL, :assigned_at)
    """
)

_INSERT_ACCESS_SQL = text(
    """
    INSERT INTO user_company_access (user_id, company_id, is_primary, role, attached_at)
    VALUES (:user_id, :company_id, :is_primary, :role, :attached_at)
    """
)


def _legacy_role_id(conn: Connection):
    """Any legacy role id, for the not-null `user_projects.role_id` column."""
    for role_name in ("manager", "admin", "member"):
        row = conn.execute(_ROLE_ID_SQL, {"role_name": role_name}).fetchone()
        if row is not None:
            return row[0]
    return None


def _warn_projects_without_company(conn: Connection, report: BackfillReport) -> None:
    """Count projects the resolver cannot answer for, and say so loudly.

    They stay reachable to platform ops only until someone sets their
    `company_id`; a later phase makes the column NOT NULL. Never abort the
    migration over it — migrations run at container start, so raising here is
    an outage.
    """
    count = conn.execute(_PROJECTS_WITHOUT_COMPANY_SQL).scalar() or 0
    report.projects_without_company = int(count)
    if report.projects_without_company:
        report.warnings.append(
            f"WARNING: {report.projects_without_company} project(s) have company_id IS NULL — "
            "nobody but platform ops can open them until a company is set on each row."
        )


def backfill_creator_assignments(conn: Connection, report: BackfillReport) -> None:
    """Step 3: every project owner gets an assignment + at least `manager` in the project's company."""
    _warn_projects_without_company(conn, report)
    legacy_role_id = _legacy_role_id(conn)

    now = datetime.now(timezone.utc)
    for project_id, owner_id, company_id in conn.execute(_PROJECTS_SQL).fetchall():
        exists = conn.execute(
            _ASSIGNMENT_EXISTS_SQL, {"user_id": str(owner_id), "project_id": str(project_id)}
        ).fetchone()
        if exists is None:
            if legacy_role_id is None:
                report.assignments_skipped_no_role += 1
            else:
                conn.execute(
                    _INSERT_ASSIGNMENT_SQL,
                    {
                        "user_id": owner_id,
                        "project_id": project_id,
                        "role_id": legacy_role_id,
                        "assigned_at": now,
                    },
                )
                report.creator_assignments += 1

        if company_id is None:
            continue
        row = next(
            (r for r in access_rows(conn, owner_id) if str(r[0]).replace("-", "") == str(company_id).replace("-", "")),
            None,
        )
        if row is None:
            conn.execute(
                _INSERT_ACCESS_SQL,
                {
                    "user_id": owner_id,
                    "company_id": company_id,
                    "is_primary": False,
                    "role": "manager",
                    "attached_at": now,
                },
            )
            report.owner_access_created += 1
            report.lines.append(
                f"  creator: {user_label(conn, owner_id)} attached as manager of {company_label(conn, company_id)}"
            )
        elif raise_role(conn, owner_id, company_id, "manager", row[1]):
            report.owner_roles_raised += 1
            report.lines.append(
                f"  creator: {user_label(conn, owner_id)} → manager of {company_label(conn, company_id)}"
            )
