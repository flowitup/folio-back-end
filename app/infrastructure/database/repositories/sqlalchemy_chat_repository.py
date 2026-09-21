"""SQLAlchemy adapters for the chat ports: messages, read markers and the membership directory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import bindparam, exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.application.chat.ports import ChannelInfo, MemberInfo
from app.domain.companies.roles import CompanyRole
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

    def answer_choice_if_unanswered(self, message_id: UUID, answered: str, answered_payload: dict[str, Any]) -> bool:
        """Single conditional UPDATE — the only defense against two concurrent
        ``POST /assistant/actions`` calls on the same choice message racing each other
        (review finding NEW-H1): a plain read-modify-write (``find_by_id`` +
        ``update_payload``) lets both requests read ``answered is None`` before either
        commits, so both would dispatch and (on a ``set_project``/``not_duplicate``
        choice) create two invoices from one receipt. The WHERE clause is evaluated by
        the database against the row's *current* state, so only the first writer's
        UPDATE can ever match; the second gets ``rowcount == 0`` and is told to raise
        ``AssistantAlreadyAnsweredError`` instead of dispatching.

        Postgres merges the two new keys into the existing JSONB without needing the
        rest of the payload; SQLite (tests) uses the JSON1 ``json_set``/``json_extract``
        functions to the same effect — both read the "not yet answered" predicate off
        the row itself, not off a value read earlier in this process.
        """
        bind = self._session.get_bind()
        is_postgres = bind is not None and bind.dialect.name == "postgresql"
        answered_payload_json = json.dumps(answered_payload)
        if is_postgres:
            sql = (
                "UPDATE chat_messages "
                "SET payload = payload || jsonb_build_object("
                "'answered', CAST(:answered AS text), "
                "'answered_payload', CAST(:answered_payload AS jsonb)"
                ") "
                "WHERE id = :id AND (payload ->> 'answered') IS NULL"
            )
        else:
            sql = (
                "UPDATE chat_messages "
                "SET payload = json_set("
                "payload, '$.answered', :answered, '$.answered_payload', json(:answered_payload)"
                ") "
                "WHERE id = :id AND json_extract(payload, '$.answered') IS NULL"
            )
        # `id` must be bound with the column's own type: on SQLite, `ChatMessageOrm.id`
        # (postgresql.UUID) stores a 32-char hex string with no dashes, which a plain
        # `str(message_id)` (dashed) would never match — silently making every call
        # here a no-op, the opposite of this method's purpose.
        stmt = text(sql).bindparams(bindparam("id", type_=ChatMessageOrm.id.type))
        result = self._session.execute(
            stmt, {"id": message_id, "answered": answered, "answered_payload": answered_payload_json}
        )
        self._session.commit()
        # `session.execute(text(...))`'s static return type is the generic `Result[Any]`
        # (a `text()` clause could be a SELECT too) even though this one is an UPDATE, so
        # mypy does not see `rowcount` — it is populated at runtime regardless of which
        # `execute()` overload constructed the `CursorResult` (same as `mark_processed`'s
        # `update()`-construct version elsewhere in this codebase).
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        """Messages the assistant was actually addressed by/as, newest first (reversed
        to oldest-first before returning) — never other chat in the channel (D18)."""
        stmt = (
            select(ChatMessageOrm)
            .where(
                ChatMessageOrm.channel_kind == channel.kind,
                ChatMessageOrm.channel_id == channel.id,
                or_(ChatMessageOrm.mentions_assistant.is_(True), ChatMessageOrm.sender_type == "assistant"),
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

    def _admin_channel_member_ids(self, company_id: UUID) -> list[UUID]:
        """Company admins + every platform-ops user — the ``admin:<company_id>``
        channel's membership (D17)."""
        admin_ids = self._session.execute(
            select(UserCompanyAccessModel.user_id).where(
                UserCompanyAccessModel.company_id == company_id,
                UserCompanyAccessModel.role == CompanyRole.ADMIN.value,
            )
        ).scalars()
        ops_ids = self._session.execute(select(UserModel.id).where(UserModel.is_platform_ops)).scalars()
        ids: list[UUID] = []
        for uid in [*admin_ids, *ops_ids]:
            if uid not in ids:
                ids.append(uid)
        return ids

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
        companies = self._session.execute(
            select(CompanyModel.id, CompanyModel.legal_name, UserCompanyAccessModel.role)
            .join(UserCompanyAccessModel, UserCompanyAccessModel.company_id == CompanyModel.id)
            .where(UserCompanyAccessModel.user_id == user_id)
            .order_by(CompanyModel.legal_name)
        ).all()
        project_stmt = select(ProjectModel.id, ProjectModel.name).order_by(ProjectModel.name)
        if not self._is_superadmin(user_id):
            visible = self._membership_project_ids(user_id)
            project_stmt = project_stmt.where((ProjectModel.id.in_(visible)) | (ProjectModel.owner_id == user_id))
        projects = self._session.execute(project_stmt).all()

        for cid, name, role in companies:
            result.append(
                ChannelInfo(
                    channel=ChannelRef(kind="company", id=cid), name=name, member_count=self._company_member_count(cid)
                )
            )
            # The admin channel is listed right after its company channel, only for that
            # company's own admins (platform ops can still read/send once they know the
            # key — see is_member/channel_exists — but the chip row is admins-only, D19).
            if role == CompanyRole.ADMIN.value:
                result.append(
                    ChannelInfo(
                        channel=ChannelRef(kind="admin", id=cid),
                        name=name,
                        member_count=len(self._admin_channel_member_ids(cid)),
                    )
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
        model = CompanyModel if channel.kind in ("company", "admin") else ProjectModel
        return bool(self._session.execute(select(exists().where(model.id == channel.id))).scalar())

    def channel_name(self, channel: ChannelRef) -> str:
        """Display name of the company / project behind the key ("" when it vanished).

        The admin channel of a company shares its plain legal name — the apps label the
        "Quản trị"/admin kind themselves from ``kind == "admin"``, not from the name.
        """
        if channel.kind == "project":
            return self._session.execute(select(ProjectModel.name).where(ProjectModel.id == channel.id)).scalar() or ""
        return (
            self._session.execute(select(CompanyModel.legal_name).where(CompanyModel.id == channel.id)).scalar() or ""
        )

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        if channel.kind == "admin":
            if self._is_superadmin(user_id):
                return True
            role = self._session.execute(
                select(UserCompanyAccessModel.role).where(
                    UserCompanyAccessModel.user_id == user_id, UserCompanyAccessModel.company_id == channel.id
                )
            ).scalar()
            return role == CompanyRole.ADMIN.value
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
        if channel.kind == "admin":
            ids = self._admin_channel_member_ids(channel.id)
        elif channel.kind == "company":
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
