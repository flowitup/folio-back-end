"""SQLAlchemy implementation of AuthzReaderPort.

Backs the company-aware permission resolver (`app.domain.authz.resolver`)
against `user_company_access`, `user_projects` (association table, no ORM
model — see `app.infrastructure.database.models.associations`), and
`projects.company_id`.

Comparisons normalize both sides to lowercase, dash-stripped text rather than
relying on the `UUID` column type's bind processor matching whatever format a
given row was written in. This matters because this codebase writes
`user_projects`/`user_company_access` rows through at least three different
paths (ORM relationship append, Core `Table.insert()`, and raw `text()` SQL
with a plain `str(uuid)` parameter) — on Postgres the native `uuid` type
normalizes all of them identically, but on SQLite (used by the whole test
suite) the column is a plain CHAR/TEXT, so a row written via one path
(dashless hex, via the type's own bind processor) will NOT string-match a
`WHERE` built from the same value via a different path (dashed literal). The
`REPLACE(LOWER(CAST(...)))` normalization is insert-path-agnostic on both
dialects, at the cost of the DB not using an index for these lookups — an
acceptable trade for Phase 1's row volumes (per-company / per-project
membership counts).

`grants_for` always returns `[]` — the `company_member_grants` table (D8)
lands in Phase 2. Every other method degrades to "no relationship" (None /
False / empty list) rather than raising, since a missing row is a normal,
expected outcome (e.g. a user with no access to a company).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def _norm(column_expr: str) -> str:
    """SQL fragment: lowercase, dash-stripped text form of a UUID column/param."""
    return f"REPLACE(LOWER(CAST({column_expr} AS TEXT)), '-', '')"


class SqlAlchemyAuthzReader:
    """Implements `AuthzReaderPort` against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        """Return the caller's role on `company_id`, or None if unattached."""
        row = self._session.execute(
            text(
                f"SELECT role FROM user_company_access "
                f"WHERE {_norm('user_id')} = {_norm(':uid')} "
                f"AND {_norm('company_id')} = {_norm(':cid')} LIMIT 1"
            ),
            {"uid": str(user_id), "cid": str(company_id)},
        ).fetchone()
        return row[0] if row is not None else None

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if `user_id` has a `user_projects` row for `project_id`."""
        row = self._session.execute(
            text(
                f"SELECT 1 FROM user_projects "
                f"WHERE {_norm('user_id')} = {_norm(':uid')} "
                f"AND {_norm('project_id')} = {_norm(':pid')} LIMIT 1"
            ),
            {"uid": str(user_id), "pid": str(project_id)},
        ).fetchone()
        return row is not None

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        """Return the `company_id` owning `project_id`, or None (missing project or orphaned FK)."""
        row = self._session.execute(
            text(f"SELECT company_id FROM projects WHERE {_norm('id')} = {_norm(':pid')} LIMIT 1"),
            {"pid": str(project_id)},
        ).fetchone()
        if row is None or row[0] is None:
            return None
        raw = row[0]
        return raw if isinstance(raw, UUID) else UUID(str(raw))

    def primary_company_id(self, user_id: UUID) -> "UUID | None":
        """Return the company_id of the user's `is_primary=True` access row, or None."""
        row = self._session.execute(
            text(
                f"SELECT company_id FROM user_company_access "
                f"WHERE {_norm('user_id')} = {_norm(':uid')} AND is_primary = TRUE LIMIT 1"
            ),
            {"uid": str(user_id)},
        ).fetchone()
        if row is None:
            return None
        raw = row[0]
        return raw if isinstance(raw, UUID) else UUID(str(raw))

    def admin_company_ids(self, user_id: UUID) -> "list[UUID]":
        """Return every company_id where `user_id` holds the "admin" role."""
        rows = self._session.execute(
            text(
                f"SELECT company_id FROM user_company_access "
                f"WHERE {_norm('user_id')} = {_norm(':uid')} AND role = 'admin'"
            ),
            {"uid": str(user_id)},
        ).fetchall()
        return [r[0] if isinstance(r[0], UUID) else UUID(str(r[0])) for r in rows]

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> "list[tuple[str, str]]":
        """D8 per-user grant/deny rows — always empty until Phase 2 ships the table."""
        return []
