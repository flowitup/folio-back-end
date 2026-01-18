"""Session manager port - interface for session management."""

from typing import Optional, Protocol
from uuid import UUID


class SessionManagerPort(Protocol):
    """Port for session management."""

    def create_session(self, user_id: UUID) -> str:
        """Create new session, return session ID."""
        ...

    def get_user_id(self, session_id: str) -> Optional[UUID]:
        """Get user ID from session. Returns None if invalid/expired."""
        ...

    def destroy_session(self, session_id: str) -> None:
        """Destroy session by ID."""
        ...
