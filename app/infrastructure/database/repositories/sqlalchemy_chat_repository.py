"""SQLAlchemy adapters for the chat ports: messages, read markers and the membership directory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from app.application.chat.ports import ChannelInfo, MemberInfo
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.infrastructure.database.models.associations import role_permissions, user_roles
from app.infrastructure.database.models.chat_message import ChatChannelReadOrm, ChatMessageOrm
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.permission import PermissionModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel


def _naive_utc(value: datetime) -> datetime:
    """Compare timestamps in one convention: SQLite stores naive values, Postgres tz-aware."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class SqlAlchemyChatRepository:
    """Implements ChatMessageRepositoryPort, ChatReadRepositoryPort and ChatDirectoryPort."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # ChatMessageRepositoryPort
    # ------------------------------------------------------------------

    def add(self, message: ChatMessage) -> None:
        self._session.add(ChatMessageOrm.from_entity(message))
        self._session.flush()

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        orm = self._session.get(ChatMessageOrm, message_id)
        return orm.to_entity() if orm is not None else None

    def list_for_channel(self, channel: ChannelRef, before: Optional[datetime], limit: int) -> list[ChatMessage]:
        stmt = select(ChatMessageOrm).where(
            ChatMessageOrm.channel_kind == channel.kind, ChatMessageOrm.channel_id == channel.id
        )
        if before is not None:
            stmt = stmt.where(ChatMessageOrm.created_at < before)
        rows = self._session.execute(stmt.order_by(ChatMessageOrm.created_at.desc()).limit(limit)).scalars().all()
        return [row.to_entity() for row in reversed(rows)]

    def count_since(self, channel: ChannelRef, since: Optional[datetime], exclude_sender: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ChatMessageOrm)
            .where(
                ChatMessageOrm.channel_kind == channel.kind,
                ChatMessageOrm.channel_id == channel.id,
                ChatMessageOrm.sender_id != exclude_sender,
            )
        )
        if since is not None:
            stmt = stmt.where(ChatMessageOrm.created_at > since)
        return int(self._session.execute(stmt).scalar_one())

    def last_message_at(self, channel: ChannelRef) -> Optional[datetime]:
        value = self._session.execute(
            select(func.max(ChatMessageOrm.created_at)).where(
                ChatMessageOrm.channel_kind == channel.kind, ChatMessageOrm.channel_id == channel.id
            )
        ).scalar_one_or_none()
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # ChatReadRepositoryPort
    # ------------------------------------------------------------------

    def last_read_at(self, user_id: UUID, channel: ChannelRef) -> Optional[datetime]:
        row = self._session.get(ChatChannelReadOrm, (user_id, channel.kind, channel.id))
        if row is None:
            return None
        return row.last_read_at if row.last_read_at.tzinfo else row.last_read_at.replace(tzinfo=timezone.utc)

    def mark_read(self, user_id: UUID, channel: ChannelRef, at: datetime) -> None:
        row = self._session.get(ChatChannelReadOrm, (user_id, channel.kind, channel.id))
        if row is None:
            self._session.add(
                ChatChannelReadOrm(user_id=user_id, channel_kind=channel.kind, channel_id=channel.id, last_read_at=at)
            )
        elif _naive_utc(row.last_read_at) < _naive_utc(at):
            row.last_read_at = at
        self._session.flush()

    # ------------------------------------------------------------------
    # ChatDirectoryPort
    # ------------------------------------------------------------------

    def _is_superadmin(self, user_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(user_roles)
            .join(role_permissions, role_permissions.c.role_id == user_roles.c.role_id)
            .join(PermissionModel, PermissionModel.id == role_permissions.c.permission_id)
            .where(user_roles.c.user_id == user_id, PermissionModel.name == "*:*")
        )
        return int(self._session.execute(stmt).scalar_one()) > 0

    def _company_member_count(self, company_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(UserCompanyAccessModel)
            .where(UserCompanyAccessModel.company_id == company_id)
        )
        return int(self._session.execute(stmt).scalar_one())

    def _membership_project_ids(self, user_id: UUID) -> list[UUID]:
        """Projects with a user_projects row for the user.

        Textual SQL with string-bound ids, like the project membership reader: the
        association table is filled by raw SQL in the test suite (SQLite), so a typed
        column comparison would not match those rows.
        """
        rows = self._session.execute(
            text("SELECT project_id FROM user_projects WHERE user_id = :uid"), {"uid": str(user_id)}
        ).fetchall()
        return [UUID(str(row[0])) for row in rows]

    def _project_member_ids(self, project_id: UUID) -> list[UUID]:
        rows = self._session.execute(
            text("SELECT user_id FROM user_projects WHERE project_id = :pid"), {"pid": str(project_id)}
        ).fetchall()
        owner = self._session.execute(select(ProjectModel.owner_id).where(ProjectModel.id == project_id)).scalar()
        ids: list[UUID] = []
        for raw in [*(row[0] for row in rows), owner]:
            if raw is None:
                continue
            uid = UUID(str(raw))
            if uid not in ids:
                ids.append(uid)
        return ids

    def list_channels_for_user(self, user_id: UUID) -> list[ChannelInfo]:
        companies = self._session.execute(
            select(CompanyModel.id, CompanyModel.legal_name)
            .join(UserCompanyAccessModel, UserCompanyAccessModel.company_id == CompanyModel.id)
            .where(UserCompanyAccessModel.user_id == user_id)
            .order_by(CompanyModel.legal_name)
        ).all()
        project_stmt = select(ProjectModel.id, ProjectModel.name).order_by(ProjectModel.name)
        if not self._is_superadmin(user_id):
            visible = self._membership_project_ids(user_id)
            project_stmt = project_stmt.where((ProjectModel.id.in_(visible)) | (ProjectModel.owner_id == user_id))
        projects = self._session.execute(project_stmt).all()

        result = [
            ChannelInfo(
                channel=ChannelRef(kind="company", id=cid),
                name=name,
                member_count=self._company_member_count(cid),
            )
            for cid, name in companies
        ]
        result.extend(
            ChannelInfo(
                channel=ChannelRef(kind="project", id=pid),
                name=name,
                member_count=len(self._project_member_ids(pid)),
            )
            for pid, name in projects
        )
        return result

    def channel_exists(self, channel: ChannelRef) -> bool:
        model = CompanyModel if channel.kind == "company" else ProjectModel
        return bool(self._session.execute(select(exists().where(model.id == channel.id))).scalar())

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        if channel.kind == "company":
            return (
                self._session.execute(
                    select(
                        exists().where(
                            UserCompanyAccessModel.user_id == user_id,
                            UserCompanyAccessModel.company_id == channel.id,
                        )
                    )
                ).scalar()
                or False
            )
        if user_id in self._project_member_ids(channel.id):
            return True
        return self._is_superadmin(user_id)

    def list_members(self, channel: ChannelRef) -> list[MemberInfo]:
        if channel.kind == "company":
            ids = list(
                self._session.execute(
                    select(UserCompanyAccessModel.user_id).where(UserCompanyAccessModel.company_id == channel.id)
                ).scalars()
            )
        else:
            ids = self._project_member_ids(channel.id)
        names = self.display_names(ids)
        return sorted((MemberInfo(id=uid, name=names.get(uid, "?")) for uid in ids), key=lambda m: m.name.lower())

    def display_names(self, user_ids: list[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        rows = self._session.execute(
            select(UserModel.id, UserModel.display_name, UserModel.email).where(UserModel.id.in_(list(set(user_ids))))
        ).all()
        return {uid: (display_name or email) for uid, display_name, email in rows}
