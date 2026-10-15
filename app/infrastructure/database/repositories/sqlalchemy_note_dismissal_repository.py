"""SQLAlchemy adapter implementing NoteDismissalRepositoryPort."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.infrastructure.database.models.note_orm import NoteDismissalOrm


class SqlAlchemyNoteDismissalRepository:
    """Implements NoteDismissalRepositoryPort for note dismissal persistence.

    Idempotent add: uses check-and-add to avoid duplicate PK inserts.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user_id: UUID, note_id: UUID) -> None:
        """Record that a user dismissed a note notification (idempotent).

        No-op if the (user_id, note_id) dismissal already exists.
        """
        existing = self._session.get(NoteDismissalOrm, (user_id, note_id))
        if existing is None:
            orm = NoteDismissalOrm(user_id=user_id, note_id=note_id)
            self._session.add(orm)
            self._session.flush()
