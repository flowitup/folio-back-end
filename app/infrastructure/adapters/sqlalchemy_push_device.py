"""SQLAlchemy adapter for push devices + who to notify about a project's attendance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import PermissionModel, ProjectModel
from app.infrastructure.database.models.associations import role_permissions, user_projects, user_roles
from app.infrastructure.database.models.push_device import PushDeviceOrm

# Same rule as the bell: owner, or a project member whose membership role / global role validates.
_VALIDATOR_PERMISSIONS = ("project:manage_labor", "project:*", "*:*")


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
        """Users who may validate attendance on the project (bell rule, see the pending query)."""
        owner = self._session.query(ProjectModel.owner_id).filter(ProjectModel.id == project_id).scalar()
        membership_grants = exists(
            select(1)
            .select_from(role_permissions.join(PermissionModel, PermissionModel.id == role_permissions.c.permission_id))
            .where(
                role_permissions.c.role_id == user_projects.c.role_id, PermissionModel.name.in_(_VALIDATOR_PERMISSIONS)
            )
        )
        global_grants = exists(
            select(1)
            .select_from(
                user_roles.join(role_permissions, role_permissions.c.role_id == user_roles.c.role_id).join(
                    PermissionModel, PermissionModel.id == role_permissions.c.permission_id
                )
            )
            .where(user_roles.c.user_id == user_projects.c.user_id, PermissionModel.name.in_(_VALIDATOR_PERMISSIONS))
        )
        members = (
            self._session.query(user_projects.c.user_id)
            .filter(user_projects.c.project_id == project_id, membership_grants | global_grants)
            .all()
        )
        ids = {row[0] for row in members}
        if owner is not None:
            ids.add(owner)
        return list(ids)
