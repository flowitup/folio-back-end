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
        """Return True if user_id is authorized to act as a member of project_id.

        Three paths grant authorization (any one is sufficient):
        1. Explicit membership row in user_projects.
        2. Project ownership (projects.owner_id) — owner is implicitly a member.
        3. Holder of the global ``*:*`` superadmin permission.

        Mirrors the owner + ``*:*`` bypass pattern already used by the
        invitations and members routes. A single union SELECT with LIMIT 1.
        """
        result = self._session.execute(
            text(
                "SELECT 1 FROM user_projects "
                "WHERE user_id = :uid AND project_id = :pid "
                "UNION ALL "
                "SELECT 1 FROM projects "
                "WHERE id = :pid AND owner_id = :uid "
                "UNION ALL "
                "SELECT 1 "
                "FROM user_roles ur "
                "JOIN role_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.name = '*:*' "
                "LIMIT 1"
            ),
            {"uid": str(user_id), "pid": str(project_id)},
        )
        return result.fetchone() is not None
