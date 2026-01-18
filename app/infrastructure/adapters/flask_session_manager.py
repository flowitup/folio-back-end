"""Flask session manager adapter."""

from typing import Dict, Optional
from uuid import UUID, uuid4


class FlaskSessionManager:
    """In-memory session manager implementation of SessionManagerPort.

    NOTE: This is a development implementation. For production, use
    Redis-backed sessions (Phase 03).
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, UUID] = {}

    def create_session(self, user_id: UUID) -> str:
        """Create new session, return session ID."""
        session_id = str(uuid4())
        self._sessions[session_id] = user_id
        return session_id

    def get_user_id(self, session_id: str) -> Optional[UUID]:
        """Get user ID from session. Returns None if invalid/expired."""
        return self._sessions.get(session_id)

    def destroy_session(self, session_id: str) -> None:
        """Destroy session by ID."""
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Clear all sessions. For testing only."""
        self._sessions.clear()
