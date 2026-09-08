"""SQLAlchemy adapter implementing ProjectMembershipReaderPort.

"Is this user a member of this project?" is answered by the permission
resolver (`app.domain.authz.resolver`), not by the `user_projects` table: a
company admin reads every project of their company without an assignment,
a booted user loses access the moment their company role is gone, and neither
project ownership nor a legacy global role is a bypass any more (D6).

Consumers (notes, project analyses, chiffrage) use this as their read gate;
their write routes additionally chain `require_project_access(write=True)`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.authz.resolver import has_permission
from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader


class SqlAlchemyProjectMembershipReader:
    """Implements ProjectMembershipReaderPort against the authz resolver."""

    def __init__(self, session: Session, authz_reader=None) -> None:
        """Args:
        session: SQLAlchemy session, used to build the default authz reader.
        authz_reader: an `AuthzReaderPort`; defaults to a reader on `session`
            (uncached — callers that want the per-request cache pass the
            container's reader).
        """
        self._session = session
        self._authz_reader = authz_reader if authz_reader is not None else SqlAlchemyAuthzReader(session)

    def is_member(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True when the caller may read `project_id`.

        `project:read` is the matrix permission behind "is a member of": company
        admins hold it on every project of their company, assigned managers and
        members on theirs, and it is never deniable. Platform ops keeps the
        support bypass, read live from `users.is_platform_ops`.
        """
        return has_permission(
            self._authz_reader,
            user_id,
            "project:read",
            project_id=project_id,
            is_platform_admin=self._authz_reader.is_platform_ops(user_id),
        )
