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

`grants_for` reads the Phase 2 `company_member_grants` table (D8). Every
method degrades to "no relationship" (None / False / empty list) rather than
raising, since a missing row is a normal, expected outcome (e.g. a user with
no access to a company, or no grant/deny row at all).
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

    def project_exists(self, project_id: UUID) -> bool:
        """Return True when `project_id` names an existing `projects` row.

        A project whose `company_id` is NULL exists but resolves to no
        permission, so existence cannot be inferred from `project_company_id`.
        Cached per request like every other read here.
        """
        cache = self._cache()
        key = ("project_exists", project_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(f"SELECT 1 FROM projects WHERE {self._eq('id', 'pid')} LIMIT 1"),
            {"pid": self._bind_uuid(project_id)},
        ).fetchone()
        result = row is not None
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

    def preload_for_projects(self, user_id: UUID, project_ids: "list[UUID]") -> None:
        """Prime the per-request cache with everything the resolver needs for N projects.

        Three queries total — project→company, the caller's assignments, the
        caller's D8 rows — instead of the three-per-project the resolver would
        otherwise issue while building `my_permissions` for a project list.
        No-op without a `cache_provider` (nothing to prime into).
        """
        cache = self._cache()
        if cache is None or not project_ids:
            return
        unique_ids = list(dict.fromkeys(project_ids))
        self.preload_project_company_ids(unique_ids)

        assigned = set(self.assigned_project_ids(user_id, unique_ids))
        for pid in unique_ids:
            cache[("is_assigned", user_id, pid)] = pid in assigned

        company_ids = [cid for cid in {cache.get(("project_company_id", pid)) for pid in unique_ids} if cid is not None]
        if not company_ids:
            return
        rows = self._grant_rows_for_scope(user_id, company_ids, unique_ids)
        for company_id in company_ids:
            company_wide = [(perm, effect) for cid, pid, perm, effect in rows if cid == company_id and pid is None]
            cache[("grants_for", user_id, company_id, None)] = company_wide
            for project_id in unique_ids:
                scoped = [(perm, effect) for cid, pid, perm, effect in rows if cid == company_id and pid == project_id]
                cache[("grants_for", user_id, company_id, project_id)] = company_wide + scoped

    def _grant_rows_for_scope(
        self, user_id: UUID, company_ids: "list[UUID]", project_ids: "list[UUID]"
    ) -> "list[tuple[UUID | None, UUID | None, str, str]]":
        """One query returning every D8 row of `user_id` in scope for these companies/projects."""
        params: dict = {"uid": self._bind_uuid(user_id)}
        params.update({f"cid{i}": self._bind_uuid(cid) for i, cid in enumerate(company_ids)})
        params.update({f"pid{i}": self._bind_uuid(pid) for i, pid in enumerate(project_ids)})
        if self._is_sqlite():
            company_col, project_col = _norm("company_id"), _norm("project_id")
            company_in = ", ".join(_norm(f":cid{i}") for i in range(len(company_ids)))
            project_in = ", ".join(_norm(f":pid{i}") for i in range(len(project_ids)))
        else:
            company_col, project_col = "company_id", "project_id"
            company_in = ", ".join(f":cid{i}" for i in range(len(company_ids)))
            project_in = ", ".join(f":pid{i}" for i in range(len(project_ids)))
        rows = self._session.execute(
            text(
                "SELECT company_id, project_id, permission, effect FROM company_member_grants "
                f"WHERE {self._eq('user_id', 'uid')} AND {company_col} IN ({company_in}) "
                f"AND (project_id IS NULL OR {project_col} IN ({project_in}))"
            ),
            params,
        ).fetchall()
        return [(self._as_uuid_or_none(r[0]), self._as_uuid_or_none(r[1]), r[2], r[3]) for r in rows]

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

    def company_roles_for(self, user_id: UUID) -> "list[tuple[UUID, str]]":
        """Return every `(company_id, role)` pair the user is attached to."""
        cache = self._cache()
        key = ("company_roles_for", user_id)
        if cache is not None and key in cache:
            return cache[key]
        rows = self._session.execute(
            text(f"SELECT company_id, role FROM user_company_access WHERE {self._eq('user_id', 'uid')}"),
            {"uid": self._bind_uuid(user_id)},
        ).fetchall()
        result = [(self._as_uuid_or_none(r[0]), r[1]) for r in rows]
        result = [(cid, role) for cid, role in result if cid is not None]
        if cache is not None:
            cache[key] = result
        return result

    def is_platform_ops(self, user_id: UUID) -> bool:
        """Return `users.is_platform_ops` for this user (False when the user is gone)."""
        cache = self._cache()
        key = ("is_platform_ops", user_id)
        if cache is not None and key in cache:
            return cache[key]
        row = self._session.execute(
            text(f"SELECT is_platform_ops FROM users WHERE {self._eq('id', 'uid')} LIMIT 1"),
            {"uid": self._bind_uuid(user_id)},
        ).fetchone()
        result = bool(row[0]) if row is not None else False
        if cache is not None:
            cache[key] = result
        return result

    def project_ids_for_company(self, company_id: UUID) -> "list[UUID]":
        """Return every project id owned by `company_id`."""
        cache = self._cache()
        key = ("project_ids_for_company", company_id)
        if cache is not None and key in cache:
            return cache[key]
        rows = self._session.execute(
            text(f"SELECT id FROM projects WHERE {self._eq('company_id', 'cid')}"),
            {"cid": self._bind_uuid(company_id)},
        ).fetchall()
        result = [self._as_uuid_or_none(r[0]) for r in rows]
        if cache is not None:
            cache[key] = result
        return result

    def assigned_project_ids(self, user_id: UUID, project_ids: "list[UUID]") -> "list[UUID]":
        """Return the subset of `project_ids` the user has a `user_projects` row for."""
        if not project_ids:
            return []
        unique_ids = list(dict.fromkeys(project_ids))
        if self._is_sqlite():
            id_col = _norm("up.project_id")
            placeholders = ", ".join(_norm(f":id{i}") for i in range(len(unique_ids)))
        else:
            id_col = "up.project_id"
            placeholders = ", ".join(f":id{i}" for i in range(len(unique_ids)))
        params = {f"id{i}": self._bind_uuid(pid) for i, pid in enumerate(unique_ids)}
        params["uid"] = self._bind_uuid(user_id)
        rows = self._session.execute(
            text(
                f"SELECT up.project_id FROM user_projects up "
                f"WHERE {self._eq('up.user_id', 'uid')} AND {id_col} IN ({placeholders})"
            ),
            params,
        ).fetchall()
        return [self._as_uuid_or_none(r[0]) for r in rows]

    def assigned_project_ids_for_users(self, company_id: UUID, user_ids: "list[UUID]") -> "dict[UUID, list[UUID]]":
        """Batch form of `assigned_project_ids` for every user of one company (H5).

        One `user_projects JOIN projects` query for the whole `user_ids`
        list, instead of the directory calling `assigned_project_ids` once
        per person. The JOIN condition mirrors `has_project_assignment_in_company`
        (dialect-normalized on SQLite for the same insert-path-agnostic reason).
        """
        result: "dict[UUID, list[UUID]]" = {}
        if not user_ids:
            return result
        unique_users = list(dict.fromkeys(user_ids))
        if self._is_sqlite():
            join_clause = f"{_norm('p.id')} = {_norm('up.project_id')}"
            user_col = _norm("up.user_id")
            placeholders = ", ".join(_norm(f":uid{i}") for i in range(len(unique_users)))
        else:
            join_clause = "p.id = up.project_id"
            user_col = "up.user_id"
            placeholders = ", ".join(f":uid{i}" for i in range(len(unique_users)))
        params = {f"uid{i}": self._bind_uuid(uid) for i, uid in enumerate(unique_users)}
        params["cid"] = self._bind_uuid(company_id)
        rows = self._session.execute(
            text(
                "SELECT up.user_id, up.project_id FROM user_projects up "
                f"JOIN projects p ON {join_clause} "
                f"WHERE {self._eq('p.company_id', 'cid')} AND {user_col} IN ({placeholders})"
            ),
            params,
        ).fetchall()
        for row in rows:
            uid = self._as_uuid_or_none(row[0])
            pid = self._as_uuid_or_none(row[1])
            if uid is None or pid is None:
                continue
            result.setdefault(uid, []).append(pid)
        return result

    def has_project_assignment_in_company(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if `user_id` has a `user_projects` row on any project of `company_id`.

        The JOIN condition (`projects.id` vs `user_projects.project_id`) is
        itself dialect-normalized, not just the WHERE clause: on SQLite,
        `projects.id` is written by the ORM's UUID TypeDecorator (dashless
        hex) while `user_projects.project_id` is written by several
        raw-`text()` insert paths elsewhere in this codebase (dashed
        `str(uuid)`) — comparing them with plain `=` silently returns zero
        rows.
        """
        cache = self._cache()
        key = ("has_project_assignment_in_company", user_id, company_id)
        if cache is not None and key in cache:
            return cache[key]
        if self._is_sqlite():
            join_clause = f"{_norm('p.id')} = {_norm('up.project_id')}"
        else:
            join_clause = "p.id = up.project_id"
        row = self._session.execute(
            text(
                "SELECT 1 FROM user_projects up "
                f"JOIN projects p ON {join_clause} "
                f"WHERE {self._eq('up.user_id', 'uid')} AND {self._eq('p.company_id', 'cid')} LIMIT 1"
            ),
            {"uid": self._bind_uuid(user_id), "cid": self._bind_uuid(company_id)},
        ).fetchone()
        result = row is not None
        if cache is not None:
            cache[key] = result
        return result

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> "list[tuple[str, str]]":
        """Return the caller's D8 grant/deny rows applicable to this scope.

        Reads `company_member_grants` for `(user_id, company_id)`, keeping
        rows that are either company-wide (`project_id IS NULL` — always
        applicable) or scoped to the SAME `project_id` passed in. A row
        scoped to a DIFFERENT project never matches — that is the whole
        point of D8's per-project scoping (a grant on project P must not
        leak onto project Q).

        Caching key includes `project_id` (even though a company-wide row
        would also apply project-agnostically) because the resolver calls
        this once per resolved `(user_id, company_id, project_id)` triple —
        caching at that same granularity avoids a second cache dimension
        for no benefit within one request.
        """
        cache = self._cache()
        key = ("grants_for", user_id, company_id, project_id)
        if cache is not None and key in cache:
            return cache[key]

        if project_id is None:
            scope_clause = "project_id IS NULL"
            params = {"uid": self._bind_uuid(user_id), "cid": self._bind_uuid(company_id)}
        else:
            scope_clause = f"(project_id IS NULL OR {self._eq('project_id', 'pid')})"
            params = {
                "uid": self._bind_uuid(user_id),
                "cid": self._bind_uuid(company_id),
                "pid": self._bind_uuid(project_id),
            }

        rows = self._session.execute(
            text(
                f"SELECT permission, effect FROM company_member_grants "
                f"WHERE {self._eq('user_id', 'uid')} AND {self._eq('company_id', 'cid')} AND {scope_clause}"
            ),
            params,
        ).fetchall()
        result = [(r[0], r[1]) for r in rows]
        if cache is not None:
            cache[key] = result
        return result

    @staticmethod
    def _as_uuid_or_none(raw) -> "UUID | None":
        if raw is None:
            return None
        return raw if isinstance(raw, UUID) else UUID(str(raw))
