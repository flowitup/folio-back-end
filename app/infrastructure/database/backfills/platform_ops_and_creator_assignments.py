"""Map legacy global roles onto the company-tenant model (ops flag + assignments).

Three data steps, written in Python so the same code runs on Postgres (deploy)
and SQLite (tests):

  1. **ops flag** — every user holding a legacy global role that carries the
     ``*:*`` permission becomes ``users.is_platform_ops = true`` and, where they
     are attached to a company, ``admin`` of their primary company (or of the
     single company they are attached to). Platform ops is a support flag, not a
     tenant role: it never creates a company attachment that did not exist.
  2. **global manager** — a user holding the legacy global ``manager`` role
     becomes ``manager`` of every company they are already attached to as
     ``member``; an existing ``admin`` row is never downgraded.
  3. **creator assignment** — the owner bypass disappears with this release
     (D6), so every project's ``owner_id`` gets a ``user_projects`` row (legacy
     ``manager`` ``role_id``, the column itself is dropped in a later phase) and
     the owner's role in the project's company is raised to at least ``manager``.
     An owner with no access row for that company gets one — otherwise the
     person who created the project would lose it at deploy time.
  4. **directory profile** — invariant: every ``user_company_access`` row has an
     active ``company_persons`` profile linked to the user. Users attached
     before the company directory shipped have no profile, so the assign-member
     pickers cannot see them. Missing ``persons`` rows are derived from the
     user's display name (or email) and phone.

Every step is idempotent: re-running changes nothing. The returned report is
printed by the migration so the mapping can be reviewed against a prod dump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

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

_USER_EMAIL_SQL = text("SELECT email FROM users WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)")

_SET_OPS_SQL = text("UPDATE users SET is_platform_ops = TRUE WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)")

_ACCESS_ROWS_SQL = text(
    """
    SELECT company_id, role, is_primary
    FROM user_company_access
    WHERE CAST(user_id AS TEXT) = CAST(:user_id AS TEXT)
    """
)

_SET_ACCESS_ROLE_SQL = text(
    """
    UPDATE user_company_access SET role = :role
    WHERE CAST(user_id AS TEXT) = CAST(:user_id AS TEXT)
      AND CAST(company_id AS TEXT) = CAST(:company_id AS TEXT)
    """
)

_INSERT_ACCESS_SQL = text(
    """
    INSERT INTO user_company_access (user_id, company_id, is_primary, role, attached_at)
    VALUES (:user_id, :company_id, :is_primary, :role, :attached_at)
    """
)

_COMPANY_NAME_SQL = text("SELECT legal_name FROM companies WHERE CAST(id AS TEXT) = CAST(:company_id AS TEXT)")

_ROLE_ID_SQL = text("SELECT id FROM roles WHERE name = :role_name")

_PROJECTS_SQL = text("SELECT id, owner_id, company_id FROM projects WHERE owner_id IS NOT NULL")

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

_ACCESS_PAIRS_SQL = text("SELECT user_id, company_id FROM user_company_access")

_USER_IDENTITY_SQL = text(
    "SELECT display_name, email, phone FROM users WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)"
)

_PERSON_BY_USER_SQL = text("SELECT id FROM persons WHERE CAST(user_id AS TEXT) = CAST(:user_id AS TEXT) LIMIT 1")

_INSERT_PERSON_SQL = text(
    """
    INSERT INTO persons (id, name, normalized_name, phone, phone_normalized, user_id, created_by_user_id, created_at)
    VALUES (:id, :name, :normalized_name, :phone, :phone_normalized, :user_id, :user_id, :created_at)
    """
)

_COMPANY_PERSON_SQL = text(
    """
    SELECT id, is_active FROM company_persons
    WHERE CAST(company_id AS TEXT) = CAST(:company_id AS TEXT)
      AND CAST(person_id AS TEXT) = CAST(:person_id AS TEXT)
    LIMIT 1
    """
)

_REACTIVATE_COMPANY_PERSON_SQL = text(
    """
    UPDATE company_persons SET is_active = TRUE, pending_expires_at = NULL
    WHERE CAST(id AS TEXT) = CAST(:id AS TEXT)
    """
)

_INSERT_COMPANY_PERSON_SQL = text(
    """
    INSERT INTO company_persons
      (id, company_id, person_id, labor_role_id, default_daily_rate, is_active,
       phone_normalized, created_by_user_id, created_at)
    VALUES (:id, :company_id, :person_id, NULL, NULL, TRUE, :phone_normalized, :user_id, :created_at)
    """
)

# Ranked weakest → strongest; a backfill only ever raises a role.
_ROLE_RANK = {"member": 0, "manager": 1, "admin": 2}


@dataclass
class BackfillReport:
    """Counts + human-readable mapping lines, printed by the migration."""

    ops_users: int = 0
    ops_company_admins: int = 0
    global_managers: int = 0
    creator_assignments: int = 0
    owner_roles_raised: int = 0
    owner_access_created: int = 0
    assignments_skipped_no_role: int = 0
    persons_created: int = 0
    profiles_created: int = 0
    profiles_reactivated: int = 0
    lines: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"platform ops + creator assignments: {self.ops_users} ops user(s), "
            f"{self.ops_company_admins} promoted to company admin, "
            f"{self.global_managers} company role(s) raised to manager from the legacy global role, "
            f"{self.creator_assignments} creator assignment(s) created, "
            f"{self.owner_roles_raised} owner company role(s) raised, "
            f"{self.owner_access_created} owner company attachment(s) created, "
            f"{self.assignments_skipped_no_role} assignment(s) skipped (no legacy role to reference), "
            f"{self.persons_created} person identity(ies) created, "
            f"{self.profiles_created} directory profile(s) created, "
            f"{self.profiles_reactivated} reactivated"
        )


def _email(conn: Connection, user_id) -> str:
    row = conn.execute(_USER_EMAIL_SQL, {"user_id": str(user_id)}).fetchone()
    return row[0] if row is not None else str(user_id)


def _company_label(conn: Connection, company_id) -> str:
    row = conn.execute(_COMPANY_NAME_SQL, {"company_id": str(company_id)}).fetchone()
    return row[0] if row is not None else str(company_id)


def _access_rows(conn: Connection, user_id) -> list:
    return list(conn.execute(_ACCESS_ROWS_SQL, {"user_id": str(user_id)}).fetchall())


def _raise_role(conn: Connection, user_id, company_id, target_role: str, current_role: str) -> bool:
    """Set the access row to `target_role` when that is a promotion. Returns True when changed."""
    if _ROLE_RANK.get(current_role, 0) >= _ROLE_RANK[target_role]:
        return False
    conn.execute(
        _SET_ACCESS_ROLE_SQL,
        {"role": target_role, "user_id": str(user_id), "company_id": str(company_id)},
    )
    return True


def backfill_platform_ops(conn: Connection, report: BackfillReport) -> None:
    """Step 1: legacy `*:*` holders → ops flag + admin of their primary/sole company."""
    for (user_id,) in conn.execute(_OPS_USERS_SQL).fetchall():
        conn.execute(_SET_OPS_SQL, {"user_id": str(user_id)})
        report.ops_users += 1
        rows = _access_rows(conn, user_id)
        target = None
        if len(rows) == 1:
            target = rows[0]
        else:
            target = next((r for r in rows if r[2]), None)
        if target is None:
            report.lines.append(f"  ops: {_email(conn, user_id)} (no company attachment — flag only)")
            continue
        company_id, role = target[0], target[1]
        if _raise_role(conn, user_id, company_id, "admin", role):
            report.ops_company_admins += 1
        report.lines.append(f"  ops: {_email(conn, user_id)} → admin of {_company_label(conn, company_id)}")


def backfill_global_managers(conn: Connection, report: BackfillReport) -> None:
    """Step 2: legacy global `manager` role → company `manager` wherever attached."""
    for (user_id,) in conn.execute(_GLOBAL_ROLE_USERS_SQL, {"role_name": "manager"}).fetchall():
        for company_id, role, _is_primary in _access_rows(conn, user_id):
            if _raise_role(conn, user_id, company_id, "manager", role):
                report.global_managers += 1
                report.lines.append(
                    f"  manager: {_email(conn, user_id)} → manager of {_company_label(conn, company_id)}"
                )


def backfill_creator_assignments(conn: Connection, report: BackfillReport) -> None:
    """Step 3: every project owner gets an assignment + at least `manager` in the project's company."""
    legacy_role_id = None
    for role_name in ("manager", "admin", "member"):
        row = conn.execute(_ROLE_ID_SQL, {"role_name": role_name}).fetchone()
        if row is not None:
            legacy_role_id = row[0]
            break

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
            (r for r in _access_rows(conn, owner_id) if str(r[0]).replace("-", "") == str(company_id).replace("-", "")),
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
                f"  creator: {_email(conn, owner_id)} attached as manager of {_company_label(conn, company_id)}"
            )
        elif _raise_role(conn, owner_id, company_id, "manager", row[1]):
            report.owner_roles_raised += 1
            report.lines.append(f"  creator: {_email(conn, owner_id)} → manager of {_company_label(conn, company_id)}")


def backfill_directory_profiles(conn: Connection, report: BackfillReport) -> None:
    """Step 4: every company attachment gets an active, user-linked directory profile.

    Without it the assign-member pickers (which list `company_persons`) cannot
    see users attached before the directory shipped.
    """
    now = datetime.now(timezone.utc)
    is_sqlite = conn.dialect.name == "sqlite"
    for user_id, company_id in conn.execute(_ACCESS_PAIRS_SQL).fetchall():
        row = conn.execute(_PERSON_BY_USER_SQL, {"user_id": str(user_id)}).fetchone()
        person_id = row[0] if row is not None else None
        phone = None
        if person_id is None:
            identity = conn.execute(_USER_IDENTITY_SQL, {"user_id": str(user_id)}).fetchone()
            if identity is None:
                continue
            display_name = identity[0] or identity[1]
            phone = identity[2]
            person_id = uuid4().hex if is_sqlite else uuid4()
            conn.execute(
                _INSERT_PERSON_SQL,
                {
                    "id": person_id,
                    "name": display_name,
                    "normalized_name": (display_name or "").strip().lower(),
                    "phone": phone,
                    "phone_normalized": phone,
                    "user_id": user_id,
                    "created_at": now,
                },
            )
            report.persons_created += 1

        profile = conn.execute(
            _COMPANY_PERSON_SQL, {"company_id": str(company_id), "person_id": str(person_id)}
        ).fetchone()
        if profile is not None:
            if not profile[1]:
                conn.execute(_REACTIVATE_COMPANY_PERSON_SQL, {"id": profile[0]})
                report.profiles_reactivated += 1
            continue
        conn.execute(
            _INSERT_COMPANY_PERSON_SQL,
            {
                "id": uuid4().hex if is_sqlite else uuid4(),
                "company_id": company_id,
                "person_id": person_id,
                "phone_normalized": phone,
                "user_id": user_id,
                "created_at": now,
            },
        )
        report.profiles_created += 1


def run_backfill(conn: Connection) -> BackfillReport:
    """Run all four steps in order and return the mapping report."""
    report = BackfillReport()
    backfill_platform_ops(conn, report)
    backfill_global_managers(conn, report)
    backfill_creator_assignments(conn, report)
    backfill_directory_profiles(conn, report)
    return report
