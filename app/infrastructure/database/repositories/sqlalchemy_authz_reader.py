"""SQLAlchemy implementation of AuthzReaderPort.

Backs the company-aware permission resolver (`app.domain.authz.resolver`)
against `user_company_access`, `user_projects` (association table, no ORM
model — see `app.infrastructure.database.models.associations`), and
`projects.company_id`.

Dialect-conditional comparisons: on PostgreSQL, `user_id`/`company_id`/
`project_id` columns are the native `uuid` type, so binding a `UUID` object
directly and comparing with plain `=` is both correct and index-friendly. On
SQLite (used by the whole test suite) these columns are plain CHAR/TEXT, and
this codebase writes them through at least three different insert paths (ORM
relationship append, Core `Table.insert()`, and raw `text()` SQL with a plain
`str(uuid)` parameter) that do NOT agree on hex-vs-dashed formatting — so on
SQLite only, both sides are normalized via `REPLACE(LOWER(CAST(...)))` before
comparing (insert-path-agnostic, at the cost of not using an index — an
acceptable trade for SQLite's test-only row volumes). `_dialect_name()` picks
the mode once per instance from the bound session.

`cache_provider` (optional) is a zero-arg callable returning a per-request
cache dict — see `app.api.v1.authz_context.get_reader_cache` — consulted by
every read method so N calls for the SAME sub-query within one request (e.g.
`company_role_for(user_id, company_id)` once per project while listing N
projects of one company) collapse to a single query. Without a provider (the
default — e.g. tests that construct this class directly), every call queries
fresh; still correct, just uncached.

`grants_for` always returns `[]` — the `company_member_grants` table (D8)
lands in Phase 2. Every other method degrades to "no relationship" (None /
False / empty list) rather than raising, since a missing row is a normal,
expected outcome (e.g. a user with no access to a company).
"""

from __future__ import annotations

from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def _norm(column_expr: str) -> str:
    """SQL fragment: lowercase, dash-stripped text form of a UUID column/param."""
    return f"REPLACE(LOWER(CAST({column_expr} AS TEXT)), '-', '')"


class SqlAlchemyAuthzReader:
    """Implements `AuthzReaderPort` against a SQLAlchemy session."""

    def __init__(self, session: Session, cache_provider: Optional[Callable[[], dict]] = None) -> None:
        self._session = session
        self._cache_provider = cache_provider
        self._cached_dialect_name: "str | None" = None

    # -- dialect / cache plumbing -----------------------------------------

    def _dialect_name(self) -> str:
        """Resolve (and cache on this instance) the bound session's dialect name."""
        if self._cached_dialect_name is None:
            self._cached_dialect_name = self._session.get_bind().dialect.name
        return self._cached_dialect_name

    def _is_sqlite(self) -> bool:
        return self._dialect_name() == "sqlite"

    def _eq(self, column: str, param: str) -> str:
        """SQL fragment comparing a UUID column to a bind param, dialect-aware."""
        if self._is_sqlite():
            return f"{_norm(column)} = {_norm(':' + param)}"
        return f"{column} = :{param}"

    def _bind_uuid(self, value: UUID):
        """Bind value for a UUID param: `str` on SQLite (plain TEXT column),
        the `UUID` object itself on Postgres (native `uuid` type, index-friendly)."""
        return str(value) if self._is_sqlite() else value

    def _cache(self) -> "dict | None":
        return self._cache_provider() if self._cache_provider is not None else None

    # -- AuthzReaderPort ----------------------------------------------------

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        """Return the caller's role on `company_id`, or None if unattached."""
        cache = self._cache()
        key = ("company_role_for", user_id, company_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(
                f"SELECT role FROM user_company_access "
                f"WHERE {self._eq('user_id', 'uid')} AND {self._eq('company_id', 'cid')} LIMIT 1"
            ),
            {"uid": self._bind_uuid(user_id), "cid": self._bind_uuid(company_id)},
        ).fetchone()
        result = row[0] if row is not None else None
        if cache is not None:
            cache[key] = result
        return result

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if `user_id` has a `user_projects` row for `project_id`."""
        cache = self._cache()
        key = ("is_assigned", user_id, project_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(
                f"SELECT 1 FROM user_projects "
                f"WHERE {self._eq('user_id', 'uid')} AND {self._eq('project_id', 'pid')} LIMIT 1"
            ),
            {"uid": self._bind_uuid(user_id), "pid": self._bind_uuid(project_id)},
        ).fetchone()
        result = row is not None
        if cache is not None:
            cache[key] = result
        return result

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        """Return the `company_id` owning `project_id`, or None (missing project or orphaned FK)."""
        cache = self._cache()
        key = ("project_company_id", project_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(f"SELECT company_id FROM projects WHERE {self._eq('id', 'pid')} LIMIT 1"),
            {"pid": self._bind_uuid(project_id)},
        ).fetchone()
        result = self._as_uuid_or_none(row[0]) if row is not None else None
        if cache is not None:
            cache[key] = result
        return result

    def preload_project_company_ids(self, project_ids: "list[UUID]") -> None:
        """Batch-fetch `company_id` for many projects in ONE query and prime the per-request cache.

        Call this once before resolving permissions for a list of N projects
        (e.g. `GET /projects`) so the per-project `project_company_id()` calls
        the resolver makes become cache hits instead of N separate SELECTs —
        the fix for the query-budget blowup a company admin with many
        projects would otherwise cause. No-op without a `cache_provider`
        (nothing to prime into) or an empty `project_ids`.
        """
        cache = self._cache()
        if cache is None or not project_ids:
            return
        unique_ids = list(dict.fromkeys(project_ids))
        params = {f"id{i}": self._bind_uuid(pid) for i, pid in enumerate(unique_ids)}
        if self._is_sqlite():
            id_col = _norm("id")
            placeholders = ", ".join(_norm(f":id{i}") for i in range(len(unique_ids)))
        else:
            id_col = "id"
            placeholders = ", ".join(f":id{i}" for i in range(len(unique_ids)))
        rows = self._session.execute(
            text(f"SELECT id, company_id FROM projects WHERE {id_col} IN ({placeholders})"),
            params,
        ).fetchall()
        resolved: "dict[UUID, UUID | None]" = {}
        for row in rows:
            pid = self._as_uuid_or_none(row[0])
            if pid is not None:
                resolved[pid] = self._as_uuid_or_none(row[1])
        for pid in unique_ids:
            cache[("project_company_id", pid)] = resolved.get(pid)

    def primary_company_id(self, user_id: UUID) -> "UUID | None":
        """Return the company_id of the user's `is_primary=True` access row, or None."""
        cache = self._cache()
        key = ("primary_company_id", user_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(
                f"SELECT company_id FROM user_company_access "
                f"WHERE {self._eq('user_id', 'uid')} AND is_primary = TRUE LIMIT 1"
            ),
            {"uid": self._bind_uuid(user_id)},
        ).fetchone()
        result = self._as_uuid_or_none(row[0]) if row is not None else None
        if cache is not None:
            cache[key] = result
        return result

    def admin_company_ids(self, user_id: UUID) -> "list[UUID]":
        """Return every company_id where `user_id` holds the "admin" role."""
        cache = self._cache()
        key = ("admin_company_ids", user_id)
        if cache is not None and key in cache:
            return cache[key]
        rows = self._session.execute(
            text(
                f"SELECT company_id FROM user_company_access " f"WHERE {self._eq('user_id', 'uid')} AND role = 'admin'"
            ),
            {"uid": self._bind_uuid(user_id)},
        ).fetchall()
        result = [self._as_uuid_or_none(r[0]) for r in rows]
        if cache is not None:
            cache[key] = result
        return result

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> "list[tuple[str, str]]":
        """D8 per-user grant/deny rows — always empty until Phase 2 ships the table."""
        return []

    @staticmethod
    def _as_uuid_or_none(raw) -> "UUID | None":
        if raw is None:
            return None
        return raw if isinstance(raw, UUID) else UUID(str(raw))
