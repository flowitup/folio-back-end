"""SQLAlchemy side of the scheduled reminders: what is due for whom, and what was sent.

The selection rules live in `app.application.push.scheduled_reminders`; this module only
answers its questions in SQL (written for PostgreSQL, the production database).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.push.project_label import project_label
from app.application.push.scheduled_reminders import (
    AttendanceProject,
    OverdueFacture,
    SentToday,
    WorkerMonth,
)
from app.domain.notifications.reminder_schedule import ACTIVE_DAYS
from app.infrastructure.database.labor_validation_scope import validator_user_ids
from app.infrastructure.database.models.billing_document import BillingDocumentModel
from app.infrastructure.database.models.labor_activity import LaborActivityModel
from app.infrastructure.database.models.labor_day_description import LaborDayDescriptionModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.notification_event import NotificationEventModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.scheduled_reminder import ScheduledReminderLogModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.models.worker import WorkerModel
from app.infrastructure.database.serializers.billing_serializers import deserialize_orm_to_doc

# Bell entries that record who touched attendance on a project (``actor_id`` in their data):
# a manager logging days is otherwise not stored anywhere on the entry itself.
_ATTENDANCE_ACTOR_KINDS = ("logged", "attendance_edited", "attendance_deleted")


def _naive_utc(moment: datetime) -> datetime:
    """For the columns stored as naive UTC (`DateTime` without timezone)."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


class SQLAlchemyReminderSource:
    """Answers `ReminderSourcePort`. The monthly labor figures come from the same use cases
    as the labor summary and payments endpoints, so they equal what the apps show."""

    def __init__(self, session: Session, monthly_summary_usecase=None, payments_summary_usecase=None) -> None:
        self._session = session
        self._monthly = monthly_summary_usecase
        self._payments = payments_summary_usecase

    # -- people ------------------------------------------------------------------------

    def _active(self, user_ids: Iterable[UUID]) -> set:
        ids = list({u for u in user_ids if u is not None})
        if not ids:
            return set()
        rows = self._session.execute(
            select(UserModel.id).where(
                UserModel.id.in_(ids), UserModel.is_active.is_(True), UserModel.deleted_at.is_(None)
            )
        ).all()
        return {row[0] for row in rows}

    def company_admins(self, company_ids: Iterable[UUID]) -> Dict[UUID, set]:
        ids = list(company_ids)
        if not ids:
            return {}
        rows = self._session.execute(
            select(UserCompanyAccessModel.company_id, UserCompanyAccessModel.user_id).where(
                UserCompanyAccessModel.company_id.in_(ids), UserCompanyAccessModel.role == "admin"
            )
        ).all()
        active = self._active(r[1] for r in rows)
        out: Dict[UUID, set] = {}
        for company_id, user_id in rows:
            if user_id in active:
                out.setdefault(company_id, set()).add(user_id)
        return out

    # -- attendance ----------------------------------------------------------------------

    def attendance_projects(self, today: date, since: datetime) -> List[AttendanceProject]:
        window = (
            select(WorkerModel.project_id)
            .join(LaborEntryModel, LaborEntryModel.worker_id == WorkerModel.id)
            .where(LaborEntryModel.date >= today - timedelta(days=ACTIVE_DAYS), LaborEntryModel.date < today)
            .distinct()
        )
        logged_today = (
            select(WorkerModel.project_id)
            .join(LaborEntryModel, LaborEntryModel.worker_id == WorkerModel.id)
            .where(LaborEntryModel.date == today)
            .distinct()
        )
        projects = (
            self._session.execute(
                select(ProjectModel).where(
                    ProjectModel.company_id.isnot(None),
                    ProjectModel.id.in_(window),
                    ProjectModel.id.notin_(logged_today),
                )
            )
            .scalars()
            .all()
        )
        out: List[AttendanceProject] = []
        for project in projects:
            validators = self._active(validator_user_ids(self._session, project.id, project.company_id))
            if not validators:
                continue
            admins = self.company_admins([project.company_id]).get(project.company_id, set())
            out.append(
                AttendanceProject(
                    project_id=project.id,
                    label=project_label(project),
                    company_id=project.company_id,
                    validators=frozenset(validators),
                    admins=frozenset(admins & validators),
                    recent_actors=frozenset(self._recent_actors(project.id, since)),
                )
            )
        return out

    def _recent_actors(self, project_id: UUID, since: datetime) -> set:
        """Who logged, validated or described days on the project since ``since``."""
        session = self._session
        actors: set = set()
        validated = session.execute(
            select(LaborEntryModel.validated_by_user_id)
            .join(WorkerModel, WorkerModel.id == LaborEntryModel.worker_id)
            .where(
                WorkerModel.project_id == project_id,
                LaborEntryModel.validated_by_user_id.isnot(None),
                LaborEntryModel.validated_at >= since,
            )
            .distinct()
        ).all()
        actors |= {row[0] for row in validated}
        naive_since = _naive_utc(since)
        for model in (LaborDayDescriptionModel, LaborActivityModel):
            rows = session.execute(
                select(model.created_by)
                .where(model.project_id == project_id, model.created_by.isnot(None), model.created_at >= naive_since)
                .distinct()
            ).all()
            actors |= {row[0] for row in rows}
        event = NotificationEventModel
        rows = session.execute(
            select(event.data["actor_id"].as_string())
            .where(
                event.kind.in_(_ATTENDANCE_ACTOR_KINDS),
                event.data["project_id"].as_string() == str(project_id),
                event.created_at >= since,
            )
            .distinct()
        ).all()
        for (raw,) in rows:
            try:
                actors.add(UUID(raw))
            except (TypeError, ValueError):
                continue
        return actors

    # -- billing ---------------------------------------------------------------------------

    def overdue_factures(self, today: date) -> List[OverdueFacture]:
        """Factures sent and not paid whose due date has passed (the web lists' rule): status
        ``sent`` or ``overdue``, a due date before today. Drafts, paid and cancelled never."""
        rows = (
            self._session.execute(
                select(BillingDocumentModel).where(
                    BillingDocumentModel.kind == "facture",
                    BillingDocumentModel.status.in_(("sent", "overdue")),
                    BillingDocumentModel.payment_due_date.isnot(None),
                    BillingDocumentModel.payment_due_date < today,
                    BillingDocumentModel.company_id.isnot(None),
                )
            )
            .scalars()
            .all()
        )
        out = []
        for row in rows:
            doc = deserialize_orm_to_doc(row)
            out.append(OverdueFacture(company_id=row.company_id, number=row.document_number, total_ttc=doc.total_ttc))
        return out

    # -- month summary ---------------------------------------------------------------------

    def companies_with_projects(self) -> List[UUID]:
        rows = self._session.execute(
            select(ProjectModel.company_id).where(ProjectModel.company_id.isnot(None)).distinct()
        ).all()
        return [row[0] for row in rows]

    def month_labor(self, company_id: UUID, year: int, month: int) -> List[WorkerMonth]:
        if self._monthly is None or self._payments is None:
            return []
        from app.application.invoice.get_labor_payments_summary_usecase import GetLaborPaymentsSummaryRequest
        from app.application.labor.get_monthly_labor_summary import GetMonthlyLaborSummaryRequest

        project_ids = [
            row[0]
            for row in self._session.execute(select(ProjectModel.id).where(ProjectModel.company_id == company_id)).all()
        ]
        out: List[WorkerMonth] = []
        for project_id in project_ids:
            names: Dict[str, str] = {}
            cells: Dict[str, List[Decimal]] = {}  # worker_id -> [days, earned, paid]
            for row in self._monthly.execute(GetMonthlyLaborSummaryRequest(project_id=project_id)).rows:
                if (row.year, row.month) != (year, month):
                    continue
                for w in row.workers:
                    names[w.worker_id] = w.worker_name
                    cell = cells.setdefault(w.worker_id, [Decimal("0")] * 3)
                    cell[0] += Decimal(str(w.days_worked))
                    cell[1] += Decimal(str(w.total_cost))
            for bucket in self._payments.execute(GetLaborPaymentsSummaryRequest(project_id=project_id)).months:
                if (bucket.year, bucket.month) != (year, month):
                    continue
                for w in bucket.workers:
                    names.setdefault(w.worker_id, w.worker_name)
                    cell = cells.setdefault(w.worker_id, [Decimal("0")] * 3)
                    cell[2] += Decimal(str(w.paid))
            out += [WorkerMonth(worker_name=names[wid], days=c[0], earned=c[1], paid=c[2]) for wid, c in cells.items()]
        return out


class SQLAlchemyReminderLog:
    """Answers `ReminderLogPort` against `scheduled_reminder_log`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sent_on(self, user_ids: Sequence[UUID], local_date: date) -> Dict[UUID, SentToday]:
        ids = list(user_ids)
        if not ids:
            return {}
        model = ScheduledReminderLogModel
        rows = self._session.execute(
            select(model.user_id, model.kind, model.pushed).where(
                model.user_id.in_(ids), model.local_date == local_date
            )
        ).all()
        out: Dict[UUID, SentToday] = {}
        for user_id, kind, pushed in rows:
            state = out.setdefault(user_id, SentToday())
            state.kinds.add(kind)
            state.pushed = state.pushed or bool(pushed)
        return out

    def claim(
        self,
        user_id: UUID,
        kinds: Sequence[str],
        local_date: date,
        *,
        pushed: bool,
        data: Dict[str, str],
        now: Optional[datetime] = None,
    ) -> bool:
        """Insert one row per kind in one transaction; False (nothing written) when another
        run already recorded any of them for that day."""
        moment = now or datetime.now(timezone.utc)
        try:
            # A savepoint: losing the race rolls back these rows only, not the caller's session.
            with self._session.begin_nested():
                for kind in kinds:
                    self._session.add(
                        ScheduledReminderLogModel(
                            id=uuid4(),
                            user_id=user_id,
                            kind=kind,
                            local_date=local_date,
                            pushed=pushed,
                            sent_at=moment,
                            data=dict(data),
                        )
                    )
        except IntegrityError:
            return False
        self._session.commit()
        return True
