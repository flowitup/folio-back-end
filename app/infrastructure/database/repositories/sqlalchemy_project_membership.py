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

    def add(self, membership: ProjectMembership) -> ProjectMembership:
        """Insert a new membership row into user_projects."""
        assigned_at = membership.assigned_at or datetime.now(timezone.utc)
        self._session.execute(
            text(
                """
                INSERT INTO user_projects
                    (user_id, project_id, role_id, invited_by_user_id, assigned_at)
                VALUES
                    (:user_id, :project_id, :role_id, :invited_by, :assigned_at)
                ON CONFLICT (user_id, project_id) DO NOTHING
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
        self._session.commit()
        return membership

    def exists(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user is already a member of the project."""
        result = self._session.execute(
            text(
                "SELECT 1 FROM user_projects WHERE user_id = :uid AND project_id = :pid LIMIT 1"
            ),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        return result.fetchone() is not None
