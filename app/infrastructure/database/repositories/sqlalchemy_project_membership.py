"""SQLAlchemy implementation of ProjectMembershipRepositoryPort."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities.project_membership import ProjectMembership


class SqlAlchemyProjectMembershipRepository:
    """SQLAlchemy adapter for ProjectMembership persistence.

    Inserts directly into the user_projects association table (extended in phase 01
    with role_id + invited_by_user_id columns).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

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
