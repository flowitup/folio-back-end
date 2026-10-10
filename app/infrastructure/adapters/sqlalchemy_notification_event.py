"""SQLAlchemy store for the bell's activity feed (see NotificationEventModel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.infrastructure.database.models.notification_event import NotificationEventModel

# Old entries are noise; trimmed whenever a new one lands for the same person.
RETENTION_DAYS = 30
FEED_LIMIT = 50


class SQLAlchemyNotificationEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        user_ids: Iterable[UUID],
        *,
        category: str,
        kind: str,
        texts: Dict[str, List[str]],
        data: Dict[str, Any],
    ) -> None:
        users = list(user_ids)
        if not users:
            return
        now = datetime.now(timezone.utc)
        try:
            self._session.query(NotificationEventModel).filter(
                NotificationEventModel.user_id.in_(users),
                NotificationEventModel.created_at < now - timedelta(days=RETENTION_DAYS),
            ).delete(synchronize_session=False)
            for user_id in users:
                self._session.add(
                    NotificationEventModel(
                        id=uuid4(),
                        user_id=user_id,
                        category=category,
                        kind=kind,
                        texts=texts,
                        data=data,
                        created_at=now,
                    )
                )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def list_for_user(self, user_id: UUID, limit: int = FEED_LIMIT) -> List[NotificationEventModel]:
        return (
            self._session.query(NotificationEventModel)
            .filter(NotificationEventModel.user_id == user_id)
            .order_by(NotificationEventModel.created_at.desc())
            .limit(limit)
            .all()
        )

    def unread_count(self, user_id: UUID) -> int:
        return (
            self._session.query(NotificationEventModel)
            .filter(NotificationEventModel.user_id == user_id, NotificationEventModel.read_at.is_(None))
            .count()
        )

    def mark_read(self, user_id: UUID, event_ids: Optional[List[UUID]] = None) -> None:
        """Mark the caller's own entries read (all of them when ``event_ids`` is None)."""
        query = self._session.query(NotificationEventModel).filter(
            NotificationEventModel.user_id == user_id, NotificationEventModel.read_at.is_(None)
        )
        if event_ids is not None:
            query = query.filter(NotificationEventModel.id.in_(event_ids))
        query.update({NotificationEventModel.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
        self._session.commit()
