"""SQLAlchemy adapter implementing ProjectMembershipReaderPort.

Read-only membership check against the user_projects association table.
Write operations live in sqlalchemy_project_membership.py.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class SqlAlchemyProjectMembershipReader:
    """Implements ProjectMembershipReaderPort: read-only membership checks.

    Uses a minimal SELECT 1 ... LIMIT 1 query — no ORM overhead needed
    for a simple existence check.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def is_member(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if user_id is an active member of project_id.

        Delegates to the user_projects association table (source of truth
        for project membership). A single point-lookup query with LIMIT 1.
        """
        result = self._session.execute(
            text("SELECT 1 FROM user_projects " "WHERE user_id = :uid AND project_id = :pid " "LIMIT 1"),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        return result.fetchone() is not None
