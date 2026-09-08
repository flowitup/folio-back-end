"""SQLAlchemy adapter for the chat push quiet window."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.infrastructure.database.models.chat_push_marker import ChatPushMarkerModel


class SQLAlchemyChatPushMarkerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def due_recipients(self, user_ids: List[UUID], channel_key: str, window_seconds: int, now: datetime) -> List[UUID]:
        """Of ``user_ids``, those not pushed about this channel within the window.

        A user with no marker has never been pushed here, so they are always due.
        """
        if not user_ids:
            return []
        cutoff = now - timedelta(seconds=window_seconds)
        recent = {
            row[0]
            for row in self._session.query(ChatPushMarkerModel.user_id)
            .filter(
                ChatPushMarkerModel.user_id.in_(user_ids),
                ChatPushMarkerModel.channel_key == channel_key,
                ChatPushMarkerModel.last_notified_at > cutoff,
            )
            .all()
        }
        return [u for u in user_ids if u not in recent]

    def mark_notified(self, user_ids: List[UUID], channel_key: str, now: datetime) -> None:
        """Open a fresh window for each user. Called only for users actually pushed."""
        if not user_ids:
            return
        existing = {
            row.user_id: row
            for row in self._session.query(ChatPushMarkerModel)
            .filter(
                ChatPushMarkerModel.user_id.in_(user_ids),
                ChatPushMarkerModel.channel_key == channel_key,
            )
            .all()
        }
        for user_id in user_ids:
            row = existing.get(user_id)
            if row is None:
                self._session.add(ChatPushMarkerModel(user_id=user_id, channel_key=channel_key, last_notified_at=now))
            else:
                row.last_notified_at = now
        try:
            self._session.commit()
        except SQLAlchemyError:
            # Two messages to the same channel in the same instant can both miss the marker
            # and both try to insert it. The loser's row already exists with a fresh
            # timestamp, so nothing is lost — but the shared request session must be
            # rolled back, or the caller's next query fails on an already-committed message.
            self._session.rollback()
