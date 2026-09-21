"""SQLAlchemy adapters for the chat ports: messages, read markers and the membership directory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.application.chat.ports import ChannelInfo, MemberInfo
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.infrastructure.database.models.chat_message import ChatChannelReadOrm, ChatMessageOrm
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

# Shown in place of an erased account's name. A single stable string rather than a
# translated one: this is an API value read by three locales, so the clients map it
# if they want it localized.
DELETED_ACCOUNT_NAME = "Deleted account"


def _naive_utc(value: datetime) -> datetime:
    """Compare timestamps in one convention: SQLite stores naive values, Postgres tz-aware."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class SqlAlchemyChatRepository:
    """Implements ChatMessageRepositoryPort, ChatReadRepositoryPort and ChatDirectoryPort."""

    def __init__(self, session: Session, assistant_enabled: Optional[Callable[[], bool]] = None) -> None:
        self._session = session
        # Whether to list "assistant:<user_id>" in list_channels_for_user. Defaults closed
        # so a repository built outside an app context (a script, a unit test) never
        # fabricates the channel.
        self._assistant_enabled: Callable[[], bool] = assistant_enabled or (lambda: False)

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
        # An assistant-authored row has sender_id NULL; in SQL `NULL != x` is NULL (not
        # true), so a plain != would silently drop every assistant reply from unread
        # counts. or_() makes a NULL sender always count as "someone else".
        stmt = (
            select(func.count())
            .select_from(ChatMessageOrm)
            .where(
                ChatMessageOrm.channel_kind == channel.kind,
                ChatMessageOrm.channel_id == channel.id,
                or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id != exclude_sender),
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

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None:
        orm = self._session.get(ChatMessageOrm, message_id)
        if orm is None:
            return
        orm.payload = payload
        self._session.flush()

    def list_recent_text(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        stmt = (
            select(ChatMessageOrm)
            .where(
                ChatMessageOrm.channel_kind == channel.kind,
                ChatMessageOrm.channel_id == channel.id,
                ChatMessageOrm.content_type == "text",
            )
            .order_by(ChatMessageOrm.created_at.desc())
            .limit(limit)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [row.to_entity() for row in reversed(rows)]

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

    def last_reads_for_channel(self, channel: ChannelRef) -> dict[UUID, datetime]:
        rows = self._session.execute(
            select(ChatChannelReadOrm).where(
                ChatChannelReadOrm.channel_kind == channel.kind,
                ChatChannelReadOrm.channel_id == channel.id,
            )
        ).scalars()
        return {
            row.user_id: row.last_read_at if row.last_read_at.tzinfo else row.last_read_at.replace(tzinfo=timezone.utc)
            for row in rows
        }

    # ------------------------------------------------------------------
    # ChatDirectoryPort
    # ------------------------------------------------------------------

    def _is_superadmin(self, user_id: UUID) -> bool:
        """Platform ops (flowitup support) — the `users.is_platform_ops` flag."""
        stmt = select(func.count()).select_from(UserModel).where(UserModel.id == user_id, UserModel.is_platform_ops)
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
        result: list[ChannelInfo] = []
        if self._assistant_enabled():
            result.append(
                ChannelInfo(channel=ChannelRef(kind="assistant", id=user_id), name="Assistant", member_count=1)
            )
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

        result.extend(
            ChannelInfo(
                channel=ChannelRef(kind="company", id=cid),
                name=name,
                member_count=self._company_member_count(cid),
            )
            for cid, name in companies
        )
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
        if channel.kind == "assistant":
            # FEATURE_ASSISTANT is the pipeline's real kill switch (not just the channel
            # listing / actions endpoint): once off, the assistant channel does not
            # exist at all, so send/list/read/attachment all answer as they would for
            # any unknown channel (404), and nothing ever reaches the AI pipeline.
            if not self._assistant_enabled():
                return False
            return bool(self._session.execute(select(exists().where(UserModel.id == channel.id))).scalar())
        model = CompanyModel if channel.kind == "company" else ProjectModel
        return bool(self._session.execute(select(exists().where(model.id == channel.id))).scalar())

    def channel_name(self, channel: ChannelRef) -> str:
        """Display name of the company / project behind the key ("" when it vanished)."""
        if channel.kind == "assistant":
            return "Assistant"
        model = CompanyModel if channel.kind == "company" else ProjectModel
        return self._session.execute(select(model.name).where(model.id == channel.id)).scalar() or ""

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        if channel.kind == "assistant":
            # The only member of a user's assistant conversation is that user — not even
            # a platform-ops superadmin can read someone else's. FEATURE_ASSISTANT off
            # means nobody is a member of any assistant channel (see channel_exists).
            return self._assistant_enabled() and user_id == channel.id
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
        if channel.kind == "assistant":
            names = self.display_names([channel.id])
            return [MemberInfo(id=channel.id, name=names.get(channel.id, "?"))]
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
        """Names to show beside messages, one lookup for the whole channel.

        An erased account has no display_name and a placeholder email built from
        its own id, so falling through to `email` would print that user's internal
        UUID to everyone else in the channel. Messages are deliberately kept when
        an account is deleted, so the sender needs a name that is neither the
        person nor their id.
        """
        if not user_ids:
            return {}
        rows = self._session.execute(
            select(UserModel.id, UserModel.display_name, UserModel.email, UserModel.deleted_at).where(
                UserModel.id.in_(list(set(user_ids)))
            )
        ).all()
        return {
            uid: (DELETED_ACCOUNT_NAME if deleted_at is not None else (display_name or email))
            for uid, display_name, email, deleted_at in rows
        }
