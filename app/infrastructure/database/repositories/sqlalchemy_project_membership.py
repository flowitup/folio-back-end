"""SQLAlchemy implementation of ProjectMembershipRepositoryPort."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities.project_membership import ProjectMembership


def _norm(column_expr: str) -> str:
    """SQL fragment: lowercase, dash-stripped text form of a UUID column/param.

    Mirrors `app.infrastructure.database.repositories.sqlalchemy_authz_reader
    ._norm` — same insert-path-agnostic normalisation, needed here for the
    same reason: `user_projects` rows are written through several different
    paths (ORM relationship append, Core `Table.insert()`, raw `text()` SQL)
    that do not agree on hex-vs-dashed formatting on SQLite.
    """
    return f"REPLACE(LOWER(CAST({column_expr} AS TEXT)), '-', '')"


class SqlAlchemyProjectMembershipRepository:
    """SQLAlchemy adapter for ProjectMembership persistence.

    Inserts directly into the user_projects association table (extended in phase 01
    with role_id + invited_by_user_id columns).
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._cached_dialect_name: "str | None" = None

    def _is_sqlite(self) -> bool:
        if self._cached_dialect_name is None:
            self._cached_dialect_name = self._session.get_bind().dialect.name
        return self._cached_dialect_name == "sqlite"

    def add(self, membership: ProjectMembership) -> bool:
        """Insert a new membership row IF NOT ALREADY PRESENT.

        Returns True if a row was actually inserted, False if the (user_id, project_id)
        pair already existed. Uses ``INSERT ... ON CONFLICT DO NOTHING RETURNING user_id``
        — RETURNING produces a row only when the INSERT succeeded; a conflict produces
        zero rows. Both Postgres and SQLite ≥3.35 support this combination.

        Used by ``BulkAddExistingUserUseCase`` to distinguish actually-added from
        already-a-member without a separate read (H1 fix from code-review).
        """
        assigned_at = membership.assigned_at or datetime.now(timezone.utc)
        result = self._session.execute(
            text(
                """
                INSERT INTO user_projects
                    (user_id, project_id, role_id, invited_by_user_id, assigned_at)
                VALUES
                    (:user_id, :project_id, :role_id, :invited_by, :assigned_at)
                ON CONFLICT (user_id, project_id) DO NOTHING
                RETURNING user_id
                """
            ),
            {
                "user_id": str(membership.user_id),
                "project_id": str(membership.project_id),
                "role_id": str(membership.role_id),
                "invited_by": str(membership.invited_by) if membership.invited_by else None,
                "assigned_at": assigned_at,
            },
        )
        inserted = result.fetchone() is not None
        self._session.flush()
        return inserted

    def exists(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user is already a member of the project."""
        result = self._session.execute(
            text("SELECT 1 FROM user_projects WHERE user_id = :uid AND project_id = :pid LIMIT 1"),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        return result.fetchone() is not None

    def find_role_id(self, user_id: UUID, project_id: UUID):
        """Return the role_id of an existing membership row, or None."""
        result = self._session.execute(
            text("SELECT role_id FROM user_projects " "WHERE user_id = :uid AND project_id = :pid LIMIT 1"),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        row = result.fetchone()
        if row is None:
            return None
        # SQLAlchemy returns string for SQLite UUID; coerce to UUID for the port contract.
        from uuid import UUID as _UUID

        raw = row[0]
        return raw if isinstance(raw, _UUID) else _UUID(str(raw))

    def remove(self, user_id: UUID, project_id: UUID) -> bool:
        """Delete a membership row (project assignment removal). Returns True if a row was deleted.

        Used by the project-assignment endpoints and by the company boot/detach
        cleanup (Phase 2 onboarding slice) to unassign a manager/member from a
        project. Flushes only (no commit) — same convention as `add()`/`delete()`
        elsewhere in this repository, so a caller can compose this with other
        writes into one atomic transaction.

        M9: on SQLite, `user_id`/`project_id` are compared normalized (like
        `SqlAlchemyAuthzReader`) — boot/detach cleanup calls this for every
        project of a company, and a plain `=` comparison silently deletes zero
        rows when the mixed insert paths in this codebase wrote a
        differently-formatted UUID string for this pair.
        """
        if self._is_sqlite():
            where_clause = f"{_norm('user_id')} = {_norm(':uid')} AND {_norm('project_id')} = {_norm(':pid')}"
        else:
            where_clause = "user_id = :uid AND project_id = :pid"
        result = self._session.execute(
            text(f"DELETE FROM user_projects WHERE {where_clause}"),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        self._session.flush()
        return result.rowcount > 0

    def set_role(self, user_id: UUID, project_id: UUID, role_id: UUID) -> bool:
        """Update an existing membership's role. Returns True if a row was updated.

        The new role takes effect immediately on the next request: project-scoped
        permission checks resolve membership-role permissions per request, so no
        token refresh is needed.
        """
        result = self._session.execute(
            text("UPDATE user_projects SET role_id = :rid WHERE user_id = :uid AND project_id = :pid"),
            {"rid": str(role_id), "uid": str(user_id), "pid": str(project_id)},
        )
        self._session.commit()
        return result.rowcount > 0
