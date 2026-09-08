"""SQLAlchemy adapter for push devices + who to notify about a project's attendance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.infrastructure.database.labor_validation_scope import validator_user_ids as _validator_user_ids
from app.infrastructure.database.models import ProjectModel
from app.infrastructure.database.models.push_device import PushDeviceOrm


class SQLAlchemyPushDeviceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, user_id: UUID, token: str, platform: str) -> None:
        """A token belongs to one account: re-registering it moves it to the current user."""
        now = datetime.now(timezone.utc)
        row = self._session.query(PushDeviceOrm).filter_by(token=token).first()
        if row is None:
            row = PushDeviceOrm(
                id=uuid4(), user_id=user_id, token=token, platform=platform, created_at=now, last_seen_at=now
            )
            self._session.add(row)
        else:
            row.user_id = user_id
            row.platform = platform
            row.last_seen_at = now
        self._session.commit()

    def delete_token(self, token: str) -> None:
        self._session.query(PushDeviceOrm).filter_by(token=token).delete()
        self._session.commit()

    def tokens_for_users(self, user_ids: List[UUID]) -> Dict[UUID, List[str]]:
        if not user_ids:
            return {}
        rows = (
            self._session.query(PushDeviceOrm.user_id, PushDeviceOrm.token)
            .filter(PushDeviceOrm.user_id.in_(user_ids))
            .all()
        )
        out: Dict[UUID, List[str]] = {}
        for user_id, token in rows:
            out.setdefault(user_id, []).append(token)
        return out

    def validator_user_ids(self, project_id: UUID) -> List[UUID]:
        """Users who may validate attendance on the project (same rule as the bell).

        Company admins plus assigned managers and D8 grant holders — see
        `app.infrastructure.database.labor_validation_scope`. Platform ops is
        deliberately excluded: support staff are not project stakeholders and
        must not receive a company's push notifications.
        """
        company_id = self._session.query(ProjectModel.company_id).filter(ProjectModel.id == project_id).scalar()
        return _validator_user_ids(self._session, project_id, company_id)
