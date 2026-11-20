"""Scheduled reminder pushes (owner decision QĐ-9).

Every other push answers an event; these answer the clock, so people have a reason to open
the app when nobody wrote to them:

- managers: "no attendance logged today" at 19:30 on working days, for the sites that logged
  attendance in the last 7 days and have nothing for today;
- company admins: overdue factures on Monday morning;
- company admins: a short summary of the month just ended, on the 1st.

The cron script runs this every 15 minutes; `reminder_schedule.due_reminders` decides from
the Paris wall clock what is due. Each send is first claimed in a log unique per
(user, kind, day), so a re-run, a late run or two overlapping runs never send twice. A
person gets at most one reminder *push* per day: when two reminders are due together they
are merged into one text, and a reminder that comes after a push already sent that day only
lands in the bell. Muting a category (or push entirely) works as for every other push: the
bell still gets the entry, the phone does not ring, and the day's push is not used up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple
from uuid import UUID

from app.application.push.dispatcher import SUPPORTED_LOCALES
from app.application.push.push_format import format_amount
from app.domain.notifications.categories import NotificationCategory
from app.domain.notifications.reminder_schedule import (
    ACTIVE_DAYS,
    PRIORITY,
    ReminderKind,
    due_reminders,
    previous_month,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# What the data source answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceProject:
    """A site that logged attendance in the last days and has nothing for today."""

    project_id: UUID
    label: str
    company_id: UUID
    #: Everyone who may log and validate attendance there right now.
    validators: frozenset[UUID]
    #: The validators who are admins of the project's company.
    admins: frozenset[UUID]
    #: Users seen logging, validating or describing days there recently.
    recent_actors: frozenset[UUID]


@dataclass(frozen=True)
class OverdueFacture:
    company_id: UUID
    number: str
    total_ttc: Decimal


@dataclass(frozen=True)
class WorkerMonth:
    """One worker row of one project for one month (as the labor summaries price it)."""

    worker_name: str
    days: Decimal
    earned: Decimal
    paid: Decimal


class ReminderSourcePort(Protocol):
    def attendance_projects(self, today: date, since: datetime) -> List[AttendanceProject]: ...
    def overdue_factures(self, today: date) -> List[OverdueFacture]: ...
    def company_admins(self, company_ids: Iterable[UUID]) -> Dict[UUID, set[UUID]]: ...
    def companies_with_projects(self) -> List[UUID]: ...
    def month_labor(self, company_id: UUID, year: int, month: int) -> List[WorkerMonth]: ...


@dataclass
class SentToday:
    kinds: set[str] = field(default_factory=set)
    pushed: bool = False


class ReminderLogPort(Protocol):
    def sent_on(self, user_ids: Sequence[UUID], local_date: date) -> Dict[UUID, SentToday]: ...
    def claim(
        self, user_id: UUID, kinds: Sequence[str], local_date: date, *, pushed: bool, data: Dict[str, str]
    ) -> bool:
        """Record the send; False when any of ``kinds`` was already recorded for that day."""
        ...


class DispatcherPort(Protocol):
    """`PushDispatcher`: pushes to those who did not mute ``category`` and writes the bell entry."""

    def dispatch(
        self,
        *,
        category: str,
        recipients: Iterable[UUID],
        data: Dict[str, str],
        render: Optional[Callable[[str], Tuple[str, str]]] = None,
    ) -> None: ...


class EventRecorderPort(Protocol):
    def record(
        self, user_ids: Iterable[UUID], *, category: str, kind: str, texts: Dict[str, List[str]], data: Dict[str, str]
    ) -> None: ...


class PreferenceReaderPort(Protocol):
    def muted_user_ids(self, user_ids: List[UUID], category: str) -> set[UUID]: ...


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def attendance_recipients(project: AttendanceProject) -> set[UUID]:
    """Who is reminded about one site.

    The people who actually logged or validated there in the last days; without any, the
    managers who may log there; company admins only when nobody else may (then they are the
    ones logging). Someone who lost the right to log is never reminded.
    """
    recent = set(project.recent_actors) & set(project.validators)
    if recent:
        return recent
    managers = set(project.validators) - set(project.admins)
    if managers:
        return managers
    return set(project.admins)


@dataclass(frozen=True)
class MonthFigures:
    days: Decimal
    earned: Decimal
    owed: Decimal
    owed_workers: int

    @property
    def empty(self) -> bool:
        return self.days == 0 and self.earned == 0 and self.owed == 0


def _person_key(name: str) -> str:
    return " ".join((name or "").split()).lower()


def month_figures(rows: Iterable[WorkerMonth]) -> MonthFigures:
    """Days, wages and wages still owed for one month, the company overview's rule.

    The same person on several projects is one person (merged by name, as the web company
    view does); what is owed is wages minus what was paid for that month, never below zero
    per person — each month stands alone, an overpayment never settles another.
    """
    people: Dict[str, List[Decimal]] = {}
    days = Decimal("0")
    for row in rows:
        days += row.days
        cell = people.setdefault(_person_key(row.worker_name), [Decimal("0"), Decimal("0")])
        cell[0] += row.earned
        cell[1] += row.paid
    earned = sum((c[0] for c in people.values()), Decimal("0"))
    owed_each = [max(Decimal("0"), c[0] - c[1]) for c in people.values()]
    owed = sum(owed_each, Decimal("0"))
    return MonthFigures(
        days=days,
        earned=earned.quantize(Decimal("0.01")),
        owed=owed.quantize(Decimal("0.01")),
        owed_workers=sum(1 for o in owed_each if o > Decimal("0.005")),
    )


# ---------------------------------------------------------------------------
# Texts (vi first; rendered per device language like every push)
# ---------------------------------------------------------------------------

_ATTENDANCE = {
    "vi": ("Chưa chấm công hôm nay", "{project} · {date}", "{count} công trình chưa chấm công · {date}"),
    "fr": ("Pointage du jour manquant", "{project} · {date}", "{count} chantiers sans pointage · {date}"),
    "en": ("No attendance logged today", "{project} · {date}", "{count} sites with no attendance · {date}"),
}

_OVERDUE_TITLE = {"vi": "Hóa đơn quá hạn", "fr": "Factures en retard", "en": "Overdue invoices"}


def _overdue_phrase(count: int, amount: str, locale: str, *, short: bool = False) -> str:
    if locale == "vi":
        return f"{count} hóa đơn quá hạn ({amount})" if short else f"{count} hóa đơn quá hạn, tổng {amount}"
    if locale == "fr":
        noun = "facture en retard" if count == 1 else "factures en retard"
        total = f"{amount}" if count == 1 else f"total {amount}"
    else:
        noun = "overdue invoice" if count == 1 else "overdue invoices"
        total = f"{amount}" if count == 1 else f"{amount} in total"
    return f"{count} {noun} ({amount})" if short else f"{count} {noun}, {total}"


_COMPANIES = {"vi": "{n} công ty", "fr": "{n} sociétés", "en": "{n} companies"}

_FR_MONTHS = "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split()
_EN_MONTHS = "January February March April May June July August September October November December".split()


def _month_title(year: int, month: int, locale: str) -> str:
    if locale == "vi":
        return f"Tổng kết tháng {month}/{year}"
    if locale == "fr":
        return f"Bilan du mois : {_FR_MONTHS[month - 1]} {year}"
    return f"{_EN_MONTHS[month - 1]} {year} summary"


def _format_days(days: Decimal, locale: str) -> str:
    value = days.quantize(Decimal("0.1")).normalize()
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if locale == "en" else text.replace(".", ",")


def _month_body(fig: MonthFigures, locale: str) -> str:
    days = _format_days(fig.days, locale)
    earned = format_amount(fig.earned, locale)
    owed = format_amount(fig.owed, locale)
    n = fig.owed_workers
    if locale == "vi":
        parts = [f"{days} ngày công", f"lương {earned}"]
        parts.append(f"còn nợ {owed} ({n} người)" if fig.owed > 0 else "đã trả đủ lương")
    elif locale == "fr":
        parts = [f"{days} {'jour pointé' if fig.days < 2 else 'jours pointés'}", f"salaires {earned}"]
        workers = "1 ouvrier" if n == 1 else f"{n} ouvriers"
        parts.append(f"reste à payer {owed} ({workers})" if fig.owed > 0 else "salaires tous payés")
    else:
        parts = [f"{days} {'day' if fig.days == 1 else 'days'} logged", f"wages {earned}"]
        workers = "1 worker" if n == 1 else f"{n} workers"
        parts.append(f"{owed} still owed ({workers})" if fig.owed > 0 else "all wages paid")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    kind: ReminderKind
    projects: List[AttendanceProject] = field(default_factory=list)
    overdue_count: int = 0
    overdue_total: Decimal = Decimal("0")
    company_ids: set[UUID] = field(default_factory=set)
    figures: Optional[MonthFigures] = None


@dataclass
class PlannedReminder:
    """One person's outcome for this run."""

    user_id: UUID
    kinds: List[str]
    category: str
    texts: Dict[str, Tuple[str, str]]
    data: Dict[str, str]
    #: push | bell_only_capped | bell_only_muted | already_sent
    action: str


@dataclass
class RunReport:
    local_now: datetime
    due: List[str]
    planned: List[PlannedReminder] = field(default_factory=list)


_CATEGORY = {
    ReminderKind.ATTENDANCE: NotificationCategory.ATTENDANCE.value,
    ReminderKind.OVERDUE_FACTURES: NotificationCategory.BILLING.value,
    ReminderKind.MONTHLY_SUMMARY: NotificationCategory.BILLING.value,
}


class SendScheduledRemindersUseCase:
    def __init__(
        self,
        source: ReminderSourcePort,
        log: ReminderLogPort,
        dispatcher: DispatcherPort,
        events: Optional[EventRecorderPort] = None,
        preferences: Optional[PreferenceReaderPort] = None,
    ) -> None:
        self._source = source
        self._log = log
        self._dispatcher = dispatcher
        self._events = events
        self._preferences = preferences

    # -- gathering --------------------------------------------------------------

    def _attendance(self, today: date, at: datetime, out: Dict[UUID, Dict[ReminderKind, _Candidate]]) -> None:
        since = at - timedelta(days=ACTIVE_DAYS)
        for project in self._source.attendance_projects(today, since):
            for user in attendance_recipients(project):
                cand = out.setdefault(user, {}).setdefault(ReminderKind.ATTENDANCE, _Candidate(ReminderKind.ATTENDANCE))
                cand.projects.append(project)

    def _overdue(self, today: date, out: Dict[UUID, Dict[ReminderKind, _Candidate]]) -> None:
        by_company: Dict[UUID, List[OverdueFacture]] = {}
        for facture in self._source.overdue_factures(today):
            by_company.setdefault(facture.company_id, []).append(facture)
        if not by_company:
            return
        admins = self._source.company_admins(by_company.keys())
        for company_id, factures in by_company.items():
            for user in admins.get(company_id, ()):
                cand = out.setdefault(user, {}).setdefault(
                    ReminderKind.OVERDUE_FACTURES, _Candidate(ReminderKind.OVERDUE_FACTURES)
                )
                cand.overdue_count += len(factures)
                cand.overdue_total += sum((f.total_ttc for f in factures), Decimal("0"))
                cand.company_ids.add(company_id)

    def _monthly(self, today: date, out: Dict[UUID, Dict[ReminderKind, _Candidate]]) -> None:
        year, month = previous_month(today)
        figures: Dict[UUID, MonthFigures] = {}
        for company_id in self._source.companies_with_projects():
            fig = month_figures(self._source.month_labor(company_id, year, month))
            if not fig.empty:
                figures[company_id] = fig
        if not figures:
            return
        admins = self._source.company_admins(figures.keys())
        for company_id, fig in figures.items():
            for user in admins.get(company_id, ()):
                cand = out.setdefault(user, {}).setdefault(
                    ReminderKind.MONTHLY_SUMMARY, _Candidate(ReminderKind.MONTHLY_SUMMARY)
                )
                prev = cand.figures
                cand.figures = (
                    fig
                    if prev is None
                    else MonthFigures(
                        days=prev.days + fig.days,
                        earned=prev.earned + fig.earned,
                        owed=prev.owed + fig.owed,
                        owed_workers=prev.owed_workers + fig.owed_workers,
                    )
                )
                cand.company_ids.add(company_id)

    # -- one person's message ---------------------------------------------------------

    @staticmethod
    def _message(
        pending: Dict[ReminderKind, _Candidate], today: date
    ) -> Tuple[List[ReminderKind], str, Callable[[str], Tuple[str, str]], Dict[str, str]]:
        """The one message for a person: the most urgent reminder, with the month summary
        and the overdue factures merged when both are due (they share a morning)."""
        primary = min(pending, key=lambda k: PRIORITY[k])
        day = today.strftime("%d/%m")
        data: Dict[str, str] = {"kind": primary.value, "date": today.isoformat()}

        if primary is ReminderKind.ATTENDANCE:
            projects = sorted(pending[primary].projects, key=lambda p: (p.label.lower(), str(p.project_id)))
            data["project_id"] = str(projects[0].project_id)
            data["project_count"] = str(len(projects))

            def render_att(locale: str) -> Tuple[str, str]:
                title, one, many = _ATTENDANCE[locale]
                if len(projects) == 1:
                    return title, one.format(project=projects[0].label, date=day)
                return title, many.format(count=len(projects), date=day)

            return [primary], _CATEGORY[primary], render_att, data

        overdue = pending.get(ReminderKind.OVERDUE_FACTURES)
        if ReminderKind.MONTHLY_SUMMARY in pending:
            monthly = pending[ReminderKind.MONTHLY_SUMMARY]
            kinds = [ReminderKind.MONTHLY_SUMMARY] + ([ReminderKind.OVERDUE_FACTURES] if overdue else [])
            year, month = previous_month(today)
            companies = set(monthly.company_ids) | (set(overdue.company_ids) if overdue else set())
            data["kind"] = ReminderKind.MONTHLY_SUMMARY.value
            data["month"] = f"{year:04d}-{month:02d}"
            if len(companies) == 1:
                data["company_id"] = str(next(iter(companies)))
            if overdue:
                data["overdue_count"] = str(overdue.overdue_count)
            fig = monthly.figures
            assert fig is not None

            def render_month(locale: str) -> Tuple[str, str]:
                body = _month_body(fig, locale)
                if overdue:
                    amount = format_amount(overdue.overdue_total, locale)
                    body += " · " + _overdue_phrase(overdue.overdue_count, amount, locale, short=True)
                if len(companies) > 1:
                    body += " · " + _COMPANIES[locale].format(n=len(companies))
                return _month_title(year, month, locale), body

            return kinds, _CATEGORY[ReminderKind.MONTHLY_SUMMARY], render_month, data

        assert overdue is not None
        data["count"] = str(overdue.overdue_count)
        if len(overdue.company_ids) == 1:
            data["company_id"] = str(next(iter(overdue.company_ids)))

        def render_overdue(locale: str) -> Tuple[str, str]:
            amount = format_amount(overdue.overdue_total, locale)
            body = _overdue_phrase(overdue.overdue_count, amount, locale)
            if len(overdue.company_ids) > 1:
                body += " · " + _COMPANIES[locale].format(n=len(overdue.company_ids))
            return _OVERDUE_TITLE[locale], body

        return [ReminderKind.OVERDUE_FACTURES], _CATEGORY[ReminderKind.OVERDUE_FACTURES], render_overdue, data

    def _muted(self, users: List[UUID], category: str) -> set[UUID]:
        if self._preferences is None or not users:
            return set()
        try:
            return set(self._preferences.muted_user_ids(users, category))
        except Exception:
            # As in the dispatcher: a preference lookup failure must not silence a reminder.
            logger.exception("reminders.preferences failed category=%s", category)
            return set()

    # -- the run ------------------------------------------------------------------------

    def execute(self, at: Optional[datetime] = None, *, dry_run: bool = False) -> RunReport:
        at = at or datetime.now(timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        due = due_reminders(at)
        report = RunReport(local_now=due.local_now, due=[k.value for k in due.kinds])
        if not due.kinds:
            return report
        today = due.today

        candidates: Dict[UUID, Dict[ReminderKind, _Candidate]] = {}
        if ReminderKind.ATTENDANCE in due.kinds:
            self._attendance(today, at, candidates)
        if ReminderKind.OVERDUE_FACTURES in due.kinds:
            self._overdue(today, candidates)
        if ReminderKind.MONTHLY_SUMMARY in due.kinds:
            self._monthly(today, candidates)
        if not candidates:
            return report

        users = sorted(candidates, key=str)
        sent = self._log.sent_on(users, today)
        messages = {}
        for user in users:
            state = sent.get(user, SentToday())
            pending = {k: c for k, c in candidates[user].items() if k.value not in state.kinds}
            if not pending:
                report.planned.append(
                    PlannedReminder(user, sorted(k.value for k in candidates[user]), "", {}, {}, "already_sent")
                )
                continue
            messages[user] = (state, self._message(pending, today))

        muted_by_category: Dict[str, set[UUID]] = {}
        for category in {m[1][1] for m in messages.values()}:
            muted_by_category[category] = self._muted([u for u, m in messages.items() if m[1][1] == category], category)

        for user, (state, (kinds, category, render, data)) in messages.items():
            texts = {locale: render(locale) for locale in SUPPORTED_LOCALES}
            if state.pushed:
                action = "bell_only_capped"
            elif user in muted_by_category.get(category, set()):
                action = "bell_only_muted"
            else:
                action = "push"
            planned = PlannedReminder(user, [k.value for k in kinds], category, texts, data, action)
            if dry_run:
                report.planned.append(planned)
                continue
            if not self._log.claim(user, planned.kinds, today, pushed=action == "push", data=data):
                planned.action = "already_sent"
                report.planned.append(planned)
                continue
            report.planned.append(planned)
            self._deliver(planned, render)
        return report

    def _deliver(self, planned: PlannedReminder, render: Callable[[str], Tuple[str, str]]) -> None:
        """Push (the dispatcher also writes the bell entry) or, past the day's push, bell only."""
        try:
            if planned.action == "push":
                self._dispatcher.dispatch(
                    category=planned.category, recipients=[planned.user_id], render=render, data=planned.data
                )
            elif self._events is not None:
                self._events.record(
                    [planned.user_id],
                    category=planned.category,
                    kind=planned.data["kind"],
                    texts={locale: list(text) for locale, text in planned.texts.items()},
                    data=planned.data,
                )
        except Exception:  # one person's failure must not stop the others
            logger.exception("reminders.deliver failed user=%s kinds=%s", planned.user_id, planned.kinds)
