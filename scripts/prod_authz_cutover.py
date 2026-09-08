"""Production cutover for the company-tenant authorization model.

Runs once after migration ``9a4c1e7b2d05`` has mapped the legacy roles. The
migration can only promote users where they are already attached, and it
flags every legacy ``*:*`` holder as platform ops; the tenant decisions
(who is a company admin, who is platform ops, which demo accounts go away)
are applied here explicitly so the result does not depend on the interim
data fixes made on production before the redesign.

Idempotent: re-running with the same arguments changes nothing. Everything
runs in one transaction; with ``--dry-run`` it is rolled back at the end.

Usage (inside the api container)::

    python -m scripts.prod_authz_cutover --company "ANN ECO CONSTRUCTION" \
        --ops ops@example.com --admin a@example.com --admin b@example.com \
        --delete demo@example.com [--dry-run]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import (
    BackfillReport,
    backfill_directory_profiles,
)


class CutoverError(Exception):
    """A named user or company could not be found; nothing is written."""


@dataclass
class CutoverReport:
    admins_attached: int = 0
    admins_raised: int = 0
    ops_set: int = 0
    ops_cleared: int = 0
    users_deleted: int = 0
    profiles_created: int = 0
    lines: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"authz cutover: {self.admins_attached} admin attachment(s) created, "
            f"{self.admins_raised} role(s) raised to admin, {self.ops_set} ops flag(s) set, "
            f"{self.ops_cleared} ops flag(s) cleared, {self.users_deleted} user(s) deleted, "
            f"{self.profiles_created} directory profile(s) created"
        )


_USER_ID_SQL = text("SELECT id FROM users WHERE lower(email) = lower(:email)")
_COMPANY_ID_SQL = text("SELECT id FROM companies WHERE legal_name = :name")
_ACCESS_SQL = text("SELECT role FROM user_company_access WHERE user_id = :user_id AND company_id = :company_id")
_HAS_PRIMARY_SQL = text("SELECT 1 FROM user_company_access WHERE user_id = :user_id AND is_primary = :true_value")
_INSERT_ACCESS_SQL = text(
    "INSERT INTO user_company_access (user_id, company_id, role, is_primary, attached_at) "
    "VALUES (:user_id, :company_id, 'admin', :is_primary, :now)"
)
_RAISE_ADMIN_SQL = text(
    "UPDATE user_company_access SET role = 'admin' WHERE user_id = :user_id AND company_id = :company_id"
)
_SET_OPS_SQL = text("UPDATE users SET is_platform_ops = :value WHERE id = :user_id AND is_platform_ops <> :value")
_DELETE_USER_SQL = text("DELETE FROM users WHERE id = :user_id")
_SNAPSHOT_SQL = text(
    "SELECT u.email, u.is_platform_ops, "
    "(SELECT string_agg(c.legal_name || ':' || a.role, ', ' ORDER BY c.legal_name) "
    " FROM user_company_access a JOIN companies c ON c.id = a.company_id WHERE a.user_id = u.id) "
    "FROM users u ORDER BY u.email"
)


def _user_id(conn: Connection, email: str):
    row = conn.execute(_USER_ID_SQL, {"email": email}).fetchone()
    if row is None:
        raise CutoverError(f"user not found: {email}")
    return row[0]


def _company_id(conn: Connection, legal_name: str):
    row = conn.execute(_COMPANY_ID_SQL, {"name": legal_name}).fetchone()
    if row is None:
        raise CutoverError(f"company not found: {legal_name}")
    return row[0]


def _id_param(conn: Connection, value):
    """Raw-SQL bind that matches the column type on both dialects."""
    return str(value) if conn.dialect.name == "sqlite" else value


def _set_ops(conn: Connection, user_id, value: bool) -> bool:
    result = conn.execute(_SET_OPS_SQL, {"value": value, "user_id": _id_param(conn, user_id)})
    return result.rowcount > 0


def _ensure_admin(conn: Connection, user_id, company_id, report: CutoverReport, email: str) -> None:
    params = {"user_id": _id_param(conn, user_id), "company_id": _id_param(conn, company_id)}
    row = conn.execute(_ACCESS_SQL, params).fetchone()
    if row is None:
        has_primary = conn.execute(_HAS_PRIMARY_SQL, {"user_id": params["user_id"], "true_value": True}).fetchone()
        conn.execute(
            _INSERT_ACCESS_SQL,
            {**params, "is_primary": has_primary is None, "now": datetime.now(timezone.utc)},
        )
        report.admins_attached += 1
        report.lines.append(f"  admin: {email} attached as admin")
    elif row[0] != "admin":
        conn.execute(_RAISE_ADMIN_SQL, params)
        report.admins_raised += 1
        report.lines.append(f"  admin: {email} raised from {row[0]} to admin")


def snapshot(conn: Connection) -> list[str]:
    """One line per user: email, ops flag, company roles."""
    return [
        f"  {email}  ops={bool(is_ops)}  {roles or '(no company)'}"
        for email, is_ops, roles in conn.execute(_SNAPSHOT_SQL).fetchall()
    ]


def apply_cutover(
    conn: Connection,
    company_legal_name: str,
    admins: list[str],
    ops: list[str],
    delete: list[str],
) -> CutoverReport:
    """Apply the tenant decisions; raises CutoverError before writing when a name is unknown."""
    report = CutoverReport()
    company_id = _company_id(conn, company_legal_name)
    admin_ids = {email: _user_id(conn, email) for email in admins}
    ops_ids = {email: _user_id(conn, email) for email in ops}
    delete_ids = {email: _user_id(conn, email) for email in delete}

    for email, user_id in admin_ids.items():
        _ensure_admin(conn, user_id, company_id, report, email)
        # Company admins are tenant admins, not platform operators (D5), unless listed as ops.
        if email not in ops_ids and _set_ops(conn, user_id, False):
            report.ops_cleared += 1
            report.lines.append(f"  ops cleared: {email}")
    for email, user_id in ops_ids.items():
        if _set_ops(conn, user_id, True):
            report.ops_set += 1
            report.lines.append(f"  ops set: {email}")
    for email, user_id in delete_ids.items():
        conn.execute(_DELETE_USER_SQL, {"user_id": _id_param(conn, user_id)})
        report.users_deleted += 1
        report.lines.append(f"  deleted: {email}")

    directory = BackfillReport()
    backfill_directory_profiles(conn, directory)
    report.profiles_created = directory.profiles_created
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", required=True, help="legal_name of the tenant company")
    parser.add_argument("--admin", action="append", default=[], help="email to make company admin (repeatable)")
    parser.add_argument("--ops", action="append", default=[], help="email to flag as platform ops (repeatable)")
    parser.add_argument("--delete", action="append", default=[], help="email of an account to delete (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print the result and roll back")
    args = parser.parse_args()

    from app import create_app, db

    app = create_app()
    with app.app_context():
        conn = db.session.connection()
        print("before:")
        print("\n".join(snapshot(conn)))
        report = apply_cutover(conn, args.company, args.admin, args.ops, args.delete)
        print(report.summary())
        print("\n".join(report.lines))
        print("after:")
        print("\n".join(snapshot(conn)))
        if args.dry_run:
            db.session.rollback()
            print("dry run: rolled back")
        else:
            db.session.commit()
            print("committed")


if __name__ == "__main__":
    main()
