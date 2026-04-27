"""Repository ports (Protocols) for the invitations application layer."""

from contextlib import AbstractContextManager
from typing import Any, Optional, Protocol
from uuid import UUID

from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.project_membership import ProjectMembership
from app.domain.entities.project import Project
from app.domain.entities.role import Role
from app.domain.entities.user import User


class TransactionalSessionPort(Protocol):
    """Minimal session contract used by AcceptInvitationUseCase.

    Conforms to SQLAlchemy ``scoped_session`` / ``Session`` (from Flask-SQLAlchemy
    or vanilla SA). Production code passes ``db.session``; tests pass a fake.
    """

    def begin_nested(self) -> AbstractContextManager[Any]:
        """Open a SAVEPOINT block as a context manager."""
        ...

    def commit(self) -> None:
        """Commit the outer transaction."""
        ...


class InvitationRepositoryPort(Protocol):
    """Persistence contract for Invitation aggregate."""

    def save(self, inv: Invitation) -> Invitation:
        """Persist an invitation (insert or update). Returns the saved instance."""
        ...

    def find_by_token_hash(self, token_hash: str) -> Optional[Invitation]:
        """Look up an invitation by its sha256 token hash. Returns None if not found."""
        ...

    def find_by_token_hash_for_update(self, token_hash: str) -> Optional[Invitation]:
        """
        Look up an invitation by its sha256 token hash, acquiring a row-level
        lock for the duration of the surrounding transaction (Postgres
        ``SELECT ... FOR UPDATE``).

        Used by AcceptInvitationUseCase to serialize concurrent accept
        attempts for the same token (M1 from code-review). On dialects that
        don't support ``FOR UPDATE`` (SQLite under tests), implementations
        may degrade to a plain SELECT — the in-memory test DB doesn't have
        concurrent transactions anyway.
        """
        ...

    def find_by_id(self, invitation_id: UUID) -> Optional[Invitation]:
        """Look up an invitation by its UUID. Returns None if not found."""
        ...

    def find_pending_by_email_and_project(self, email: str, project_id: UUID) -> Optional[Invitation]:
        """
        Return the first PENDING invitation for the given email + project combination,
        or None if no such invitation exists.
        """
        ...

    def list_by_project(
        self,
        project_id: UUID,
        status: Optional[InvitationStatus] = None,
    ) -> list[Invitation]:
        """
        Return all invitations for a project, optionally filtered by status.

        Args:
            project_id: Target project UUID.
            status: If provided, only return invitations with this status.
        """
        ...

    def count_created_today_by_project(self, project_id: UUID) -> int:
        """
        Return the number of invitations created today (UTC) for the given project.

        Used to enforce the per-project daily rate limit (50/day).
        """
        ...


class ProjectMembershipRepositoryPort(Protocol):
    """Persistence contract for ProjectMembership aggregate."""

    def add(self, membership: ProjectMembership) -> ProjectMembership:
        """Persist a new project membership. Returns the saved instance."""
        ...

    def exists(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user is already a member of the project."""
        ...

    def find_role_id(self, user_id: UUID, project_id: UUID) -> Optional[UUID]:
        """
        Return the role_id of an existing (user, project) membership, or None
        if no such membership exists. Used by CreateInvitationUseCase to
        distinguish 'not a member' from 'already a member with same/different role'.
        """
        ...


class ProjectRepositoryPort(Protocol):
    """Minimal read-only project contract needed by invitation use-cases."""

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        """Look up a project by UUID. Returns None if not found."""
        ...


class RoleRepositoryPort(Protocol):
    """Minimal read-only role contract needed by invitation use-cases."""

    def find_by_id(self, role_id: UUID) -> Optional[Role]:
        """Look up a role by UUID. Returns None if not found."""
        ...


class UserWriteRepositoryPort(Protocol):
    """Write contract for user persistence used during invitation acceptance."""

    def find_by_id(self, user_id: UUID) -> Optional[User]:
        """Find a user by UUID. Returns None if not found."""
        ...

    def find_by_email(self, email: str) -> Optional[User]:
        """Find a user by email. Returns None if not found."""
        ...

    def save(self, user: User) -> User:
        """Persist a user (insert or update). Returns the saved instance."""
        ...

    def search_by_email_or_name(self, query: str, limit: int = 20) -> list[User]:
        """Search users by email or display_name (case-insensitive prefix/substring match).

        Used by the superadmin user-search endpoint (phase 03). Declared here at
        the port level so the application layer never imports from infrastructure.

        Returns up to ``limit`` matching User entities, ordered by email asc.
        """
        ...
