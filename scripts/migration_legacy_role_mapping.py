"""Legacy global roles → platform-ops flag and company roles (steps 1 and 2).

  1. **ops flag** — every user holding a legacy global role that carries the
     ``*:*`` permission becomes ``users.is_platform_ops = true`` and, where they
     are attached to a company, ``admin`` of their primary company (or of the
     single company they are attached to). Platform ops is a support flag, not a
     tenant role: it never creates a company attachment that did not exist.
  2. **global manager** — a user holding the legacy global ``manager`` role
     becomes ``manager`` of every company they are already attached to as
     ``member``; an existing ``admin`` row is never downgraded.

Both steps are idempotent: re-running changes nothing.

This lives outside ``app/`` because it reads ``user_roles`` /
``role_permissions`` / ``roles`` / ``permissions``, which revision
``c2b8f1a0d743`` drops: the queries are only valid while upgrading through
``9a4c1e7b2d05``, its one caller. It stays an importable module (rather than
inline in that revision) so the mapping keeps its unit tests. It is not under
``migrations/`` because that package name is shadowed by ``tests/migrations``
when pytest puts ``tests/`` on ``sys.path``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import (
    BackfillReport,
    access_rows,
    company_label,
    raise_role,
    user_label,
)

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
