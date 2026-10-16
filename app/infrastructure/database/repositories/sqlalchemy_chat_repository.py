"""SQLAlchemy adapters for the chat ports: messages, read markers and the membership directory."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, bindparam, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.application.chat.ports import ChannelInfo, MemberInfo, ReactionInfo, ReportInfo
from app.application.push.project_label import project_label
from app.application.usecases.otp_login import is_placeholder_email
from app.domain.companies.roles import CompanyRole
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.infrastructure.database.models.chat_message import (
    ChatChannelReadOrm,
    ChatMessageOrm,
    ChatMessageReactionOrm,
    ChatMessageReportOrm,
    ChatUserBlockOrm,
)
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

# Shown in place of an erased account's name. A single stable string rather than a
# translated one: this is an API value read by three locales, so the clients map it
# if they want it localized.
DELETED_ACCOUNT_NAME = "Deleted account"
# Shown for a phone sign-up without a display name: their only e-mail is a placeholder that embeds
# their number, which no colleague should read.
UNNAMED_ACCOUNT_NAME = "Unnamed member"


def _naive_utc(value: datetime) -> datetime:
    """Compare timestamps in one convention: SQLite stores naive values, Postgres tz-aware."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _aware_utc(value: Any) -> Optional[datetime]:
    """A stored timestamp as an aware UTC datetime (raw SQLite rows come back as strings)."""
    if value is None:
        return None
    parsed: datetime = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _ProjectLabel:
    """The two fields ``project_label`` reads, from a column tuple."""

    def __init__(self, name: Optional[str], address: Optional[str]) -> None:
        self.name = name or ""
        self.address = address


def _email_handle(email: Optional[str]) -> str:
    """``jean.dupont`` for ``jean.dupont@example.com``; the whole value when it has no ``@``."""
    value = (email or "").strip()
    handle = value.split("@", 1)[0].strip()
    return handle or value or UNNAMED_ACCOUNT_NAME


def _chunks(items: list[Any], size: int = 200) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


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

    def list_for_channel(
        self,
        channel: ChannelRef,
        before: Optional[datetime],
        limit: int,
        exclude_senders: Optional[set[UUID]] = None,
    ) -> list[ChatMessage]:
        stmt = select(ChatMessageOrm).where(
            ChatMessageOrm.channel_kind == channel.kind, ChatMessageOrm.channel_id == channel.id
        )
        if exclude_senders:
            # Before the LIMIT: a page must hold ``limit`` messages the reader can see, so a
            # blocked sender's burst never empties the history.
            stmt = stmt.where(
                or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id.notin_(list(exclude_senders)))
            )
        if before is not None:
            stmt = stmt.where(ChatMessageOrm.created_at < before)
        rows = self._session.execute(stmt.order_by(ChatMessageOrm.created_at.desc()).limit(limit)).scalars().all()
        return [row.to_entity() for row in reversed(rows)]

    def count_since(
        self,
        channel: ChannelRef,
        since: Optional[datetime],
        exclude_sender: UUID,
        exclude_senders: Optional[set[UUID]] = None,
    ) -> int:
        # Legacy rows with a NULL sender_id (from a retired feature) must still count as
        # "someone else": in SQL `NULL != x` is NULL (not true), hence the explicit or_().
        stmt = (
            select(func.count())
            .select_from(ChatMessageOrm)
            .where(
                ChatMessageOrm.channel_kind == channel.kind,
                ChatMessageOrm.channel_id == channel.id,
                or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id != exclude_sender),
            )
        )
        if exclude_senders:
            stmt = stmt.where(or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id.notin_(exclude_senders)))
        if since is not None:
            stmt = stmt.where(ChatMessageOrm.created_at > since)
        return int(self._session.execute(stmt).scalar_one())

    def count_since_many(
        self,
        thresholds: dict[ChannelRef, Optional[datetime]],
        exclude_sender: UUID,
        exclude_senders: Optional[set[UUID]] = None,
    ) -> dict[str, int]:
        """``count_since`` for many channels at once (one grouped query per 200 channels), by key."""
        result: dict[str, int] = {}
        for chunk in _chunks(list(thresholds.items())):
            conditions = []
            for channel, since in chunk:
                cond = and_(ChatMessageOrm.channel_kind == channel.kind, ChatMessageOrm.channel_id == channel.id)
                if since is not None:
                    cond = and_(cond, ChatMessageOrm.created_at > since)
                conditions.append(cond)
            stmt = (
                select(ChatMessageOrm.channel_kind, ChatMessageOrm.channel_id, func.count())
                .where(
                    or_(*conditions),
                    or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id != exclude_sender),
                )
                .group_by(ChatMessageOrm.channel_kind, ChatMessageOrm.channel_id)
            )
            if exclude_senders:
                stmt = stmt.where(
                    or_(ChatMessageOrm.sender_id.is_(None), ChatMessageOrm.sender_id.notin_(list(exclude_senders)))
                )
            for kind, cid, count in self._session.execute(stmt).all():
                result[ChannelRef(kind=kind, id=cid).key] = int(count)
        return result

    def last_message_at_many(self, channels: list[ChannelRef]) -> dict[str, datetime]:
        """Newest message time of each channel that has one, by key."""
        wanted = {c.key for c in channels}
        result: dict[str, datetime] = {}
        for chunk in _chunks(list({c.id for c in channels})):
            rows = self._session.execute(
                select(ChatMessageOrm.channel_kind, ChatMessageOrm.channel_id, func.max(ChatMessageOrm.created_at))
                .where(ChatMessageOrm.channel_id.in_(chunk))
                .group_by(ChatMessageOrm.channel_kind, ChatMessageOrm.channel_id)
            ).all()
            for kind, cid, newest in rows:
                key = ChannelRef(kind=kind, id=cid).key
                if key in wanted and newest is not None:
                    result[key] = _aware_utc(newest)  # type: ignore[assignment]
        return result

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    def _insert(self) -> Any:
        """The dialect's INSERT, which supports ON CONFLICT on Postgres and SQLite alike."""
        return pg_insert if self._session.get_bind().dialect.name == "postgresql" else sqlite_insert

    def set_reaction(self, message_id: UUID, user_id: UUID, emoji: str) -> None:
        # One statement, so two simultaneous taps cannot both INSERT (UniqueViolation -> 500).
        stmt = self._insert()(ChatMessageReactionOrm.__table__).values(
            message_id=message_id, user_id=user_id, emoji=emoji, created_at=datetime.now(timezone.utc)
        )
        self._session.execute(
            stmt.on_conflict_do_update(index_elements=["message_id", "user_id"], set_={"emoji": emoji})
        )
        self._session.flush()

    def remove_reaction(self, message_id: UUID, user_id: UUID) -> None:
        row = self._session.get(ChatMessageReactionOrm, (message_id, user_id))
        if row is not None:
            self._session.delete(row)
            self._session.flush()

    def reactions_for_messages(self, message_ids: list[UUID]) -> dict[UUID, list[ReactionInfo]]:
        if not message_ids:
            return {}
        rows = self._session.execute(
            select(ChatMessageReactionOrm)
            .where(ChatMessageReactionOrm.message_id.in_(message_ids))
            .order_by(ChatMessageReactionOrm.created_at)
            # An upsert just changed rows behind the identity map's back.
            .execution_options(populate_existing=True)
        ).scalars()
        result: dict[UUID, list[ReactionInfo]] = {}
        for row in rows:
            created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
            result.setdefault(row.message_id, []).append(
                ReactionInfo(user_id=row.user_id, emoji=row.emoji, created_at=created)
            )
        return result

    # ------------------------------------------------------------------
    # Reports and blocks
    # ------------------------------------------------------------------

    def add_report(self, message_id: UUID, reporter_id: UUID, reason: Optional[str]) -> bool:
        """Idempotent: a second report of the same message by the same person is a no-op.

        Returns True when this call stored a new report. A single INSERT .. ON CONFLICT DO
        NOTHING, so a double tap never collides on the unique constraint."""
        stmt = (
            self._insert()(ChatMessageReportOrm.__table__)
            .values(
                id=uuid4(),
                message_id=message_id,
                reporter_id=reporter_id,
                reason=reason,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["message_id", "reporter_id"])
        )
        inserted = getattr(self._session.execute(stmt), "rowcount", 0)
        self._session.flush()
        return bool(inserted)

    def list_open_reports(self, channels: list[ChannelRef]) -> list[ReportInfo]:
        """Unresolved reports on messages of the given channels, newest first."""
        if not channels:
            return []
        conditions = [and_(ChatMessageOrm.channel_kind == c.kind, ChatMessageOrm.channel_id == c.id) for c in channels]
        rows = self._session.execute(
            select(ChatMessageReportOrm, ChatMessageOrm)
            .join(ChatMessageOrm, ChatMessageOrm.id == ChatMessageReportOrm.message_id)
            .where(ChatMessageReportOrm.resolved_at.is_(None), or_(*conditions))
            .order_by(ChatMessageReportOrm.created_at.desc())
        ).all()
        return [
            ReportInfo(
                id=report.id,
                message=message.to_entity(),
                reporter_id=report.reporter_id,
                reason=report.reason,
                created_at=_aware_utc(report.created_at),  # type: ignore[arg-type]
            )
            for report, message in rows
        ]

    def find_report(self, report_id: UUID) -> Optional[ReportInfo]:
        row = self._session.execute(
            select(ChatMessageReportOrm, ChatMessageOrm)
            .join(ChatMessageOrm, ChatMessageOrm.id == ChatMessageReportOrm.message_id)
            .where(ChatMessageReportOrm.id == report_id)
        ).first()
        if row is None:
            return None
        report, message = row
        return ReportInfo(
            id=report.id,
            message=message.to_entity(),
            reporter_id=report.reporter_id,
            reason=report.reason,
            created_at=_aware_utc(report.created_at),  # type: ignore[arg-type]
            resolved_at=_aware_utc(report.resolved_at),
        )

    def resolve_reports_of_message(self, message_id: UUID, at: datetime) -> None:
        """Close every open report of the message (one moderation decision covers them all)."""
        for row in self._session.execute(
            select(ChatMessageReportOrm).where(
                ChatMessageReportOrm.message_id == message_id, ChatMessageReportOrm.resolved_at.is_(None)
            )
        ).scalars():
            row.resolved_at = at
        self._session.flush()

    def delete_message(self, message_id: UUID) -> None:
        """Remove a message for everyone (its reactions and reports go with it; replies keep their text)."""
        row = self._session.get(ChatMessageOrm, message_id)
        if row is None:
            return
        # Explicit, not only the FK cascades: SQLite (tests) does not enforce them by default.
        self._session.query(ChatMessageReactionOrm).filter_by(message_id=message_id).delete()
        self._session.query(ChatMessageReportOrm).filter_by(message_id=message_id).delete()
        self._session.query(ChatMessageOrm).filter_by(reply_to_id=message_id).update({"reply_to_id": None})
        self._session.delete(row)
        self._session.flush()

    def block(self, blocker_id: UUID, blocked_id: UUID) -> None:
        stmt = (
            self._insert()(ChatUserBlockOrm.__table__)
            .values(blocker_id=blocker_id, blocked_id=blocked_id, created_at=datetime.now(timezone.utc))
            .on_conflict_do_nothing(index_elements=["blocker_id", "blocked_id"])
        )
        self._session.execute(stmt)
        self._session.flush()

    def unblock(self, blocker_id: UUID, blocked_id: UUID) -> None:
        row = self._session.get(ChatUserBlockOrm, (blocker_id, blocked_id))
        if row is not None:
            self._session.delete(row)
            self._session.flush()

    def blocked_ids(self, blocker_id: UUID) -> set[UUID]:
        rows = self._session.execute(
            select(ChatUserBlockOrm.blocked_id).where(ChatUserBlockOrm.blocker_id == blocker_id)
        ).scalars()
        return set(rows)

    def blocker_ids_of(self, blocked_id: UUID) -> set[UUID]:
        rows = self._session.execute(
            select(ChatUserBlockOrm.blocker_id).where(ChatUserBlockOrm.blocked_id == blocked_id)
        ).scalars()
        return set(rows)

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

    def last_reads_for_user(self, user_id: UUID) -> dict[str, datetime]:
        """Every read marker of the user, by channel key (one query for the channel list)."""
        rows = self._session.execute(select(ChatChannelReadOrm).where(ChatChannelReadOrm.user_id == user_id)).scalars()
        return {
            ChannelRef(kind=row.channel_kind, id=row.channel_id).key: _aware_utc(row.last_read_at)  # type: ignore[misc]
            for row in rows
        }

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

    def _project_member_map(self, project_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        """Members of each project: users assigned to it and the company's admins (who see every
        project of their company), kept only while they hold a role in the project's company.

        A user who left or was removed from the company keeps the assignment no longer, and the
        creator is a member only while assigned (as everywhere the resolver decides: no company
        role, no assignment, no access; the creator is assigned when the project is created).
        A fixed number of queries whatever the number of projects (the channel list is polled)."""
        if not project_ids:
            return {}
        project_company: dict[UUID, UUID] = {}
        for chunk in _chunks(list(project_ids)):
            for pid, cid in self._session.execute(
                select(ProjectModel.id, ProjectModel.company_id).where(ProjectModel.id.in_(chunk))
            ).all():
                project_company[pid] = cid
        company_members: dict[UUID, set[UUID]] = {}
        company_admins: dict[UUID, list[UUID]] = {}
        for chunk in _chunks(list({c for c in project_company.values() if c is not None})):
            for uid, cid, role in self._session.execute(
                select(
                    UserCompanyAccessModel.user_id, UserCompanyAccessModel.company_id, UserCompanyAccessModel.role
                ).where(UserCompanyAccessModel.company_id.in_(chunk))
            ).all():
                company_members.setdefault(cid, set()).add(uid)
                if role == CompanyRole.ADMIN.value:
                    company_admins.setdefault(cid, []).append(uid)
        # Textual SQL with string-bound ids, like the project membership reader: the association
        # table is filled by raw SQL in the test suite (SQLite), so a typed comparison would miss rows.
        assigned: dict[UUID, list[UUID]] = {}
        for chunk in _chunks([str(p) for p in project_company]):
            rows = self._session.execute(
                text("SELECT project_id, user_id FROM user_projects WHERE project_id IN :pids").bindparams(
                    bindparam("pids", expanding=True)
                ),
                {"pids": chunk},
            ).fetchall()
            for raw_pid, raw_uid in rows:
                if raw_uid is not None:
                    assigned.setdefault(UUID(str(raw_pid)), []).append(UUID(str(raw_uid)))
        result: dict[UUID, list[UUID]] = {}
        for pid, cid in project_company.items():
            members = company_members.get(cid, set())
            ids: list[UUID] = []
            for uid in [*assigned.get(pid, []), *company_admins.get(cid, [])]:
                if uid in members and uid not in ids:
                    ids.append(uid)
            result[pid] = ids
        return result

    def _project_member_ids(self, project_id: UUID) -> list[UUID]:
        return self._project_member_map([project_id]).get(project_id, [])

    def list_channels_for_user(self, user_id: UUID) -> list[ChannelInfo]:
        """Company channels of every company the user belongs to, then the project channels of
        the projects they are a member of (assigned, or admin of the project's company).

        Platform ops get no implicit seat: a support account that is not part of a customer's
        company neither sees, reads nor writes that customer's project conversations."""
        result: list[ChannelInfo] = []
        companies = self._session.execute(
            select(CompanyModel.id, CompanyModel.legal_name, UserCompanyAccessModel.role)
            .join(UserCompanyAccessModel, UserCompanyAccessModel.company_id == CompanyModel.id)
            .where(UserCompanyAccessModel.user_id == user_id)
            .order_by(CompanyModel.legal_name)
        ).all()
        visible = self._membership_project_ids(user_id)
        projects = self._session.execute(
            select(ProjectModel.id, ProjectModel.name, ProjectModel.address).where(
                (ProjectModel.id.in_(visible))
                | ProjectModel.company_id.in_(
                    [cid for cid, _name, role in companies if role == CompanyRole.ADMIN.value]
                ),
                # Only projects of a company the user still belongs to (see _project_member_map).
                ProjectModel.company_id.in_([cid for cid, _name, _role in companies]),
            )
        ).all()
        company_counts: dict[UUID, int] = {}
        if companies:
            company_counts = {
                cid: int(count)
                for cid, count in self._session.execute(
                    select(UserCompanyAccessModel.company_id, func.count())
                    .where(UserCompanyAccessModel.company_id.in_([cid for cid, _n, _r in companies]))
                    .group_by(UserCompanyAccessModel.company_id)
                ).all()
            }
        project_members = self._project_member_map([pid for pid, _name, _address in projects])

        for cid, name, _role in companies:
            result.append(
                ChannelInfo(
                    channel=ChannelRef(kind="company", id=cid), name=name, member_count=company_counts.get(cid, 0)
                )
            )
        # Named like everywhere else in the apps and in pushes (site address, else the name), and
        # sorted by that same label so the list matches the project switcher.
        labelled = sorted(
            ((pid, project_label(_ProjectLabel(name, address))) for pid, name, address in projects),
            key=lambda item: item[1].casefold(),
        )
        result.extend(
            ChannelInfo(
                channel=ChannelRef(kind="project", id=pid),
                name=label,
                member_count=len(project_members.get(pid, [])),
            )
            for pid, label in labelled
        )
        return result

    def joined_at_many(self, user_id: UUID, channels: list[ChannelRef]) -> dict[str, datetime]:
        """When the user gained access to each channel, as far as the data tells: the company
        attachment for a company channel, the later of the attachment and the project assignment
        for a project channel. Unread counting starts there for a channel never opened, so a
        newcomer is not greeted with the whole history as unread."""
        attached = {
            cid: _aware_utc(at)
            for cid, at in self._session.execute(
                select(UserCompanyAccessModel.company_id, UserCompanyAccessModel.attached_at).where(
                    UserCompanyAccessModel.user_id == user_id
                )
            ).all()
        }
        assigned = {
            UUID(str(raw_pid)): _aware_utc(at)
            for raw_pid, at in self._session.execute(
                text("SELECT project_id, assigned_at FROM user_projects WHERE user_id = :uid"), {"uid": str(user_id)}
            ).fetchall()
        }
        project_ids = [c.id for c in channels if c.kind == "project"]
        project_company: dict[UUID, UUID] = {}
        for chunk in _chunks(project_ids):
            for pid, cid in self._session.execute(
                select(ProjectModel.id, ProjectModel.company_id).where(ProjectModel.id.in_(chunk))
            ).all():
                project_company[pid] = cid
        result: dict[str, datetime] = {}
        for channel in channels:
            if channel.kind == "project":
                company_id = project_company.get(channel.id)
                candidates = [attached.get(company_id) if company_id else None, assigned.get(channel.id)]
            else:
                candidates = [attached.get(channel.id)]
            known = [c for c in candidates if c is not None]
            if known:
                result[channel.key] = max(known)
        return result

    def channel_company_id(self, channel: ChannelRef) -> Optional[UUID]:
        """The company a channel belongs to (None when it vanished)."""
        if channel.kind in ("company", "admin"):
            return channel.id
        return self._session.execute(select(ProjectModel.company_id).where(ProjectModel.id == channel.id)).scalar()

    def company_admin_ids(self, company_id: UUID) -> list[UUID]:
        return list(
            self._session.execute(
                select(UserCompanyAccessModel.user_id).where(
                    UserCompanyAccessModel.company_id == company_id,
                    UserCompanyAccessModel.role == CompanyRole.ADMIN.value,
                )
            ).scalars()
        )

    def company_channels(self, company_id: UUID) -> list[ChannelRef]:
        """The company channel and every project channel of the company."""
        project_ids = self._session.execute(
            select(ProjectModel.id).where(ProjectModel.company_id == company_id)
        ).scalars()
        return [ChannelRef(kind="company", id=company_id), *(ChannelRef(kind="project", id=p) for p in project_ids)]

    def channel_exists(self, channel: ChannelRef) -> bool:
        model = CompanyModel if channel.kind in ("company", "admin") else ProjectModel
        return bool(self._session.execute(select(exists().where(model.id == channel.id))).scalar())

    def channel_name(self, channel: ChannelRef) -> str:
        """Display name of the company / project behind the key ("" when it vanished).

        A project is named like everywhere else (site address, else its name). The admin channel
        of a company shares its plain legal name — the apps label the "Quản trị"/admin kind
        themselves from ``kind == "admin"``, not from the name.
        """
        if channel.kind == "project":
            row = self._session.execute(
                select(ProjectModel.name, ProjectModel.address).where(ProjectModel.id == channel.id)
            ).first()
            return project_label(_ProjectLabel(row[0], row[1])) if row is not None else ""
        return (
            self._session.execute(select(CompanyModel.legal_name).where(CompanyModel.id == channel.id)).scalar() or ""
        )

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        if channel.kind == "admin":
            # The admin channel was retired: no one is a member, so it is neither
            # listed, readable, writable nor pushed. Old messages stay in the table.
            return False
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
        # Same rule as the company channel: membership only, no platform-ops shortcut.
        return user_id in self._project_member_ids(channel.id)

    def shares_channel(self, user_id: UUID, other_id: UUID) -> bool:
        """True when both users can read at least one common channel."""
        return any(self.is_member(other_id, info.channel) for info in self.list_channels_for_user(user_id))

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
        person nor their id. The same goes for a phone sign-up's placeholder address,
        which spells the phone number. A person without a display name is shown by
        the part of their address before the "@": co-workers recognise them without
        the chat handing everyone in the channel their full email address.
        """
        if not user_ids:
            return {}
        rows = self._session.execute(
            select(UserModel.id, UserModel.display_name, UserModel.email, UserModel.deleted_at).where(
                UserModel.id.in_(list(set(user_ids)))
            )
        ).all()
        return {
            uid: (
                DELETED_ACCOUNT_NAME
                if deleted_at is not None
                else (display_name or "").strip()
                or (UNNAMED_ACCOUNT_NAME if is_placeholder_email(email) else _email_handle(email))
            )
            for uid, display_name, email, deleted_at in rows
        }

    def account_states(self, user_ids: list[UUID]) -> dict[UUID, str]:
        """``"deleted"`` / ``"unnamed"`` for the ids ``display_names`` shows by a placeholder."""
        if not user_ids:
            return {}
        rows = self._session.execute(
            select(UserModel.id, UserModel.display_name, UserModel.email, UserModel.deleted_at).where(
                UserModel.id.in_(list(set(user_ids)))
            )
        ).all()
        states: dict[UUID, str] = {}
        for uid, display_name, email, deleted_at in rows:
            if deleted_at is not None:
                states[uid] = "deleted"
            elif not (display_name or "").strip() and is_placeholder_email(email):
                states[uid] = "unnamed"
        return states
