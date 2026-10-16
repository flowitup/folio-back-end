"""SQLAlchemy store for the bell's activity feed (see NotificationEventModel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Dict, Iterable, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, func, not_, or_
from sqlalchemy.orm import Query, Session

from app.infrastructure.database.models.notification_event import NotificationEventModel

# Old entries are noise; trimmed whenever a new one lands for the same person.
RETENTION_DAYS = 30
FEED_LIMIT = 50

# Attendance waiting for a decision is listed (and counted) by the bell's own "to validate"
# section, which clears once the day is settled. Older rows of these kinds duplicated it,
# so they never show in the activity feed.
HIDDEN_KINDS = ("submitted", "change_requested")

# Entries that tell the person they lost access: they stay visible even though the project
# (or company) they name can no longer be opened.
ACCESS_LOSS_KINDS = ("project_member_removed", "company_member_removed", "project_deleted")


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

    def _unread(
        self,
        user_id: UUID,
        hidden_projects: Collection[str] = (),
        hidden_companies: Collection[str] = (),
    ) -> Query:
        """The caller's unread entries the feed shows: not a hidden kind, and not about a
        project / company in ``hidden_*`` (ones the caller can no longer open)."""
        model = NotificationEventModel
        query = self._session.query(model).filter(
            model.user_id == user_id,
            model.read_at.is_(None),
            model.kind.notin_(HIDDEN_KINDS),
        )
        gone = []
        if hidden_projects:
            # coalesce: an entry naming no project has NULL here, and NOT (NULL IN ...) is NULL.
            gone.append(func.coalesce(model.data["project_id"].as_string(), "").in_(list(hidden_projects)))
        if hidden_companies:
            gone.append(func.coalesce(model.data["company_id"].as_string(), "").in_(list(hidden_companies)))
        if gone:
            query = query.filter(or_(model.kind.in_(ACCESS_LOSS_KINDS), not_(or_(*gone))))
        return query

    def list_for_user(
        self,
        user_id: UUID,
        limit: int = FEED_LIMIT,
        *,
        hidden_projects: Collection[str] = (),
        hidden_companies: Collection[str] = (),
    ) -> List[NotificationEventModel]:
        """Unread entries only: once read (opened or "mark all read") an entry leaves the bell."""
        return (
            self._unread(user_id, hidden_projects, hidden_companies)
            .order_by(NotificationEventModel.created_at.desc())
            .limit(limit)
            .all()
        )

    def unread_count(
        self,
        user_id: UUID,
        *,
        hidden_projects: Collection[str] = (),
        hidden_companies: Collection[str] = (),
    ) -> int:
        return self._unread(user_id, hidden_projects, hidden_companies).count()

    def unread_scopes(self, user_id: UUID) -> tuple[set[str], set[str]]:
        """Distinct project ids and company ids named by the caller's unread entries."""
        model = NotificationEventModel
        rows = (
            self._session.query(model.data["project_id"].as_string(), model.data["company_id"].as_string())
            .filter(model.user_id == user_id, model.read_at.is_(None), model.kind.notin_(ACCESS_LOSS_KINDS))
            .distinct()
            .all()
        )
        projects = {p for p, _ in rows if p}
        companies = {c for _, c in rows if c}
        return projects, companies

    def mark_read_matching(
        self,
        *,
        kinds: Collection[str],
        match: Dict[str, str],
        user_ids: Optional[Collection[UUID]] = None,
        before: Optional[datetime] = None,
    ) -> int:
        """Mark unread entries of ``kinds`` whose data carries every ``match`` value read.

        ``user_ids`` narrows it to those people (everyone otherwise); ``before`` to entries
        created up to that instant. Used when the thing an entry is about has been dealt with
        elsewhere (the chat was read, the task went to someone else)."""
        model = NotificationEventModel
        conditions = [model.read_at.is_(None), model.kind.in_(list(kinds))]
        conditions += [model.data[key].as_string() == str(value) for key, value in match.items()]
        if user_ids is not None:
            users = list(user_ids)
            if not users:
                return 0
            conditions.append(model.user_id.in_(users))
        if before is not None:
            conditions.append(model.created_at <= before)
        try:
            ids = [row.id for row in self._session.query(model.id).filter(and_(*conditions)).all()]
            if not ids:
                return 0
            self._session.query(model).filter(model.id.in_(ids)).update(
                {model.read_at: datetime.now(timezone.utc)}, synchronize_session=False
            )
            self._session.commit()
            return len(ids)
        except Exception:
            self._session.rollback()
            raise

    def delete_matching(self, *, match: Dict[str, str], keep_kinds: Collection[str] = ()) -> int:
        """Delete every user's entries whose data carries every ``match`` value (except ``keep_kinds``).

        Used when what the entries name is gone for good: a deleted project, an erased account."""
        model = NotificationEventModel
        conditions = [model.data[key].as_string() == str(value) for key, value in match.items()]
        if keep_kinds:
            conditions.append(model.kind.notin_(list(keep_kinds)))
        try:
            ids = [row.id for row in self._session.query(model.id).filter(and_(*conditions)).all()]
            if ids:
                self._session.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
                self._session.commit()
            return len(ids)
        except Exception:
            self._session.rollback()
            raise

    def mark_read(self, user_id: UUID, event_ids: Optional[List[UUID]] = None) -> None:
        """Mark the caller's own entries read (all of them when ``event_ids`` is None)."""
        query = self._session.query(NotificationEventModel).filter(
            NotificationEventModel.user_id == user_id, NotificationEventModel.read_at.is_(None)
        )
        if event_ids is not None:
            query = query.filter(NotificationEventModel.id.in_(event_ids))
        query.update({NotificationEventModel.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
        self._session.commit()
