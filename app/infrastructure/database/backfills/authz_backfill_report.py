"""Shared report and row helpers for the authz backfill steps.

The mapping the migration prints lands in CI/CD and container logs, so people
are identified by a masked email plus their user id — enough to audit a line
against the database, without copying addresses into a log stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Connection

_USER_IDENTITY_SQL = text(
    "SELECT display_name, email, phone FROM users WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)"
)

_USER_EMAIL_SQL = text("SELECT email FROM users WHERE CAST(id AS TEXT) = CAST(:user_id AS TEXT)")

_COMPANY_NAME_SQL = text("SELECT legal_name FROM companies WHERE CAST(id AS TEXT) = CAST(:company_id AS TEXT)")

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

# Ranked weakest → strongest; a backfill only ever raises a role.
_ROLE_RANK = {"member": 0, "manager": 1, "admin": 2}


@dataclass
class BackfillReport:
    """Counts, mapping lines and warnings, printed by the migration."""

    ops_users: int = 0
    ops_company_admins: int = 0
    global_managers: int = 0
    creator_assignments: int = 0
    owner_roles_raised: int = 0
    owner_access_created: int = 0
    projects_without_company: int = 0
    persons_created: int = 0
    profiles_created: int = 0
    profiles_reactivated: int = 0
    profiles_without_phone: int = 0
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"platform ops + creator assignments: {self.ops_users} ops user(s), "
            f"{self.ops_company_admins} promoted to company admin, "
            f"{self.global_managers} company role(s) raised to manager from the legacy global role, "
            f"{self.creator_assignments} creator assignment(s) created, "
            f"{self.owner_roles_raised} owner company role(s) raised, "
            f"{self.owner_access_created} owner company attachment(s) created, "
            f"{self.persons_created} person identity(ies) created, "
            f"{self.profiles_created} directory profile(s) created, "
            f"{self.profiles_reactivated} reactivated, "
            f"{self.profiles_without_phone} created without a phone (already taken in that company)"
        )


def mask_email(email: "str | None", user_id) -> str:
    """`ad…@flowitup.com (id=…)` — identifiable for an audit, not a copy of the address."""
    if not email or "@" not in email:
        return f"user (id={user_id})"
    local, _, domain = email.partition("@")
    return f"{local[:2]}…@{domain} (id={user_id})"


def user_label(conn: Connection, user_id) -> str:
    """Masked identity of a user, for a log line."""
    row = conn.execute(_USER_EMAIL_SQL, {"user_id": str(user_id)}).fetchone()
    return mask_email(row[0] if row is not None else None, user_id)


def user_identity(conn: Connection, user_id):
    """`(display_name, email, phone)` for a user, or None when the row is gone."""
    return conn.execute(_USER_IDENTITY_SQL, {"user_id": str(user_id)}).fetchone()


def company_label(conn: Connection, company_id) -> str:
    row = conn.execute(_COMPANY_NAME_SQL, {"company_id": str(company_id)}).fetchone()
    return row[0] if row is not None else str(company_id)


def access_rows(conn: Connection, user_id) -> list:
    """Every `(company_id, role, is_primary)` this user is attached to."""
    return list(conn.execute(_ACCESS_ROWS_SQL, {"user_id": str(user_id)}).fetchall())


def raise_role(conn: Connection, user_id, company_id, target_role: str, current_role: str) -> bool:
    """Set the access row to `target_role` when that is a promotion. Returns True when changed."""
    if _ROLE_RANK.get(current_role, 0) >= _ROLE_RANK[target_role]:
        return False
    conn.execute(
        _SET_ACCESS_ROLE_SQL,
        {"role": target_role, "user_id": str(user_id), "company_id": str(company_id)},
    )
    return True
