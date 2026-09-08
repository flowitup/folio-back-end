"""SQLAlchemy adapter for notification preferences (read for dispatch, read/write for the API)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.notifications.categories import ALL_CATEGORIES, is_valid_category
from app.infrastructure.database.models.notification_preference import NotificationPreferenceModel


class SQLAlchemyNotificationPreferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def muted_user_ids(self, user_ids: List[UUID], category: str) -> set[UUID]:
        """Users who turned ``category`` (or push entirely) off.

        Only rows that actually opt out are fetched — users without a row want everything,
        which is the overwhelming majority, so this stays a small indexed read.
        """
        if not user_ids or not is_valid_category(category):
            return set()
        column = getattr(NotificationPreferenceModel, category)
        rows = (
            self._session.query(NotificationPreferenceModel.user_id)
            .filter(
                NotificationPreferenceModel.user_id.in_(user_ids),
                (NotificationPreferenceModel.push_enabled.is_(False)) | (column.is_(False)),
            )
            .all()
        )
        return {row[0] for row in rows}

    def get(self, user_id: UUID) -> Dict[str, bool]:
        """Current settings, filled with the all-on default when the user has no row."""
        row = self._session.get(NotificationPreferenceModel, user_id)
        if row is None:
            return {"push_enabled": True, **{c: True for c in ALL_CATEGORIES}}
        return {
            "push_enabled": row.push_enabled,
            **{c: getattr(row, c) for c in ALL_CATEGORIES},
        }

    def update(self, user_id: UUID, changes: Dict[str, bool]) -> Dict[str, bool]:
        """Apply a partial update; unknown keys are rejected by the schema, not here."""
        row = self._session.get(NotificationPreferenceModel, user_id)
        now = datetime.now(timezone.utc)
        if row is None:
            row = NotificationPreferenceModel(user_id=user_id, updated_at=now)
            self._session.add(row)
        for key, value in changes.items():
            if key == "push_enabled" or is_valid_category(key):
                setattr(row, key, value)
        row.updated_at = now
        self._session.commit()
        return self.get(user_id)
