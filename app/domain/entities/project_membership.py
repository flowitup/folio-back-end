"""ProjectMembership domain entity — records a user's membership in a project."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID


@dataclass
class ProjectMembership:
    """
    Records that a user belongs to a project with a specific role.

    invited_by is nullable — memberships created outside the invitation flow
    (e.g. direct assignment by an admin) will have invited_by=None.
    """

    user_id: UUID
    project_id: UUID
    role_id: UUID
    assigned_at: datetime
    invited_by: Optional[UUID] = None

    @classmethod
    def create(
        cls,
        user_id: UUID,
        project_id: UUID,
        role_id: UUID,
        invited_by: Optional[UUID] = None,
    ) -> "ProjectMembership":
        """
        Factory for a new project membership, assigned at the current UTC time.

        Args:
            user_id: UUID of the user being added.
            project_id: UUID of the project.
            role_id: UUID of the role granted.
            invited_by: UUID of the inviting user, or None for direct assignment.
        """
        return cls(
            user_id=user_id,
            project_id=project_id,
            role_id=role_id,
            assigned_at=datetime.now(timezone.utc),
            invited_by=invited_by,
        )
