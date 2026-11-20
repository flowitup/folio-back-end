"""Scheduled reminders: who gets what, one push a day, never twice."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.application.push.dispatcher import PushDispatcher
from app.application.push.scheduled_reminders import (
    AttendanceProject,
    OverdueFacture,
    SendScheduledRemindersUseCase,
    SentToday,
    WorkerMonth,
    attendance_recipients,
    month_figures,
)

MON_1935 = datetime(2026, 11, 23, 18, 35, tzinfo=timezone.utc)  # Monday 19:35 Paris
MON_0905 = datetime(2026, 11, 23, 8, 5, tzinfo=timezone.utc)  # Monday 09:05 Paris
FIRST_MON_0905 = datetime(2026, 6, 1, 7, 5, tzinfo=timezone.utc)  # Monday 1 June, 09:05 Paris
FIRST_TUE_0905 = datetime(2026, 12, 1, 8, 5, tzinfo=timezone.utc)
SUN_1935 = datetime(2026, 11, 22, 18, 35, tzinfo=timezone.utc)


def _plain(text):
    """Amounts use no-break spaces (`format_amount`); compare with plain ones."""
    if isinstance(text, tuple):
        return tuple(_plain(t) for t in text)
    return text.replace("\xa0", " ").replace("\u202f", " ")


def _project(label="Chantier A", validators=(), admins=(), recent=()):
    return AttendanceProject(
        project_id=uuid4(),
        label=label,
        company_id=uuid4(),
        validators=frozenset(validators),
        admins=frozenset(admins),
        recent_actors=frozenset(recent),
    )


class FakeSource:
    def __init__(self, projects=(), factures=(), admins=None, labor=None):
        self.projects = list(projects)
        self.factures = list(factures)
        self.admins = admins or {}
        self.labor = labor or {}
        self.asked = []

    def attendance_projects(self, today, since):
        self.asked.append(("attendance", today, since))
        return self.projects

    def overdue_factures(self, today):
        return self.factures

    def company_admins(self, company_ids):
        return {c: set(self.admins.get(c, ())) for c in company_ids}

    def companies_with_projects(self):
        return list(self.labor)

    def month_labor(self, company_id, year, month):
        self.asked.append(("month", company_id, year, month))
        return self.labor.get(company_id, [])


class FakeLog:
    def __init__(self):
        self.rows = {}  # (user, kind, day) -> pushed

    def sent_on(self, user_ids, local_date):
        out = {}
        for (user, kind, day), pushed in self.rows.items():
            if user in user_ids and day == local_date:
                state = out.setdefault(user, SentToday())
                state.kinds.add(kind)
                state.pushed = state.pushed or pushed
        return out

    def claim(self, user_id, kinds, local_date, *, pushed, data):
        if any((user_id, k, local_date) in self.rows for k in kinds):
            return False
        for k in kinds:
            self.rows[(user_id, k, local_date)] = pushed
        return True


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, *, category, recipients, data, render=None, **kwargs):
        self.calls.append({"category": category, "recipients": list(recipients), "data": data, "render": render})


class FakeEvents:
    def __init__(self):
        self.records = []

    def record(self, user_ids, *, category, kind, texts, data):
        self.records.append({"users": list(user_ids), "category": category, "kind": kind, "texts": texts})


class FakePreferences:
    def __init__(self, muted=None):
        self.muted = muted or {}

    def muted_user_ids(self, user_ids, category):
        return {u for u in user_ids if category in self.muted.get(u, ())}


def _usecase(source, log=None, preferences=None):
    log = log or FakeLog()
    dispatcher = FakeDispatcher()
    events = FakeEvents()
    uc = SendScheduledRemindersUseCase(source, log, dispatcher, events=events, preferences=preferences)
    return uc, log, dispatcher, events


# -- who is reminded about attendance ------------------------------------------------------


def test_recent_loggers_are_preferred():
    manager, admin, other_manager = uuid4(), uuid4(), uuid4()
    p = _project(validators={manager, admin, other_manager}, admins={admin}, recent={admin})
    assert attendance_recipients(p) == {admin}


def test_without_recent_loggers_the_managers_are_reminded_not_the_admins():
    manager, admin = uuid4(), uuid4()
    p = _project(validators={manager, admin}, admins={admin})
    assert attendance_recipients(p) == {manager}


def test_admins_are_reminded_when_nobody_else_may_log():
    admin = uuid4()
    p = _project(validators={admin}, admins={admin})
    assert attendance_recipients(p) == {admin}


def test_someone_who_lost_the_right_to_log_is_not_reminded():
    manager, former = uuid4(), uuid4()
    p = _project(validators={manager}, recent={former})
    assert attendance_recipients(p) == {manager}


def test_one_push_per_manager_even_with_several_sites():
    manager, other = uuid4(), uuid4()
    a = _project("Bravo", validators={manager})
    b = _project("Alpha", validators={manager})
    c = _project("Charlie", validators={other})
    uc, log, dispatcher, _ = _usecase(FakeSource(projects=[a, b, c]))

    report = uc.execute(MON_1935)

    by_user = {call["recipients"][0]: call for call in dispatcher.calls}
    assert set(by_user) == {manager, other}
    many = by_user[manager]
    assert many["category"] == "attendance"
    assert many["data"]["kind"] == "reminder_attendance"
    assert many["data"]["date"] == "2026-11-23"
    assert many["data"]["project_count"] == "2"
    assert many["data"]["project_id"] == str(b.project_id)  # first by name
    assert many["render"]("vi") == ("Chưa chấm công hôm nay", "2 công trình chưa chấm công · 23/11")
    assert many["render"]("fr")[1] == "2 chantiers sans pointage · 23/11"
    one = by_user[other]
    assert one["render"]("vi") == ("Chưa chấm công hôm nay", "Charlie · 23/11")
    assert one["render"]("en") == ("No attendance logged today", "Charlie · 23/11")
    assert {p.action for p in report.planned} == {"push"}


def test_the_source_is_asked_about_the_last_seven_days():
    uc, _, _, _ = _usecase(source := FakeSource())
    uc.execute(MON_1935)
    _, today, since = source.asked[0]
    assert str(today) == "2026-11-23"
    assert (MON_1935 - since).days == 7


# -- idempotency, the daily cap, mute -----------------------------------------------------------


def test_a_second_run_sends_nothing_again():
    manager = uuid4()
    source = FakeSource(projects=[_project(validators={manager})])
    log = FakeLog()
    uc, _, dispatcher, events = _usecase(source, log)
    uc.execute(MON_1935)
    assert len(dispatcher.calls) == 1

    uc2, _, dispatcher2, events2 = _usecase(source, log)
    report = uc2.execute(datetime(2026, 11, 23, 18, 50, tzinfo=timezone.utc))
    assert dispatcher2.calls == [] and events2.records == []
    assert [p.action for p in report.planned] == ["already_sent"]


def test_a_late_run_still_sends_and_after_22h_nothing():
    manager = uuid4()
    source = FakeSource(projects=[_project(validators={manager})])
    uc, _, dispatcher, _ = _usecase(source)
    uc.execute(datetime(2026, 11, 23, 20, 55, tzinfo=timezone.utc))  # 21:55 Paris
    assert len(dispatcher.calls) == 1

    uc2, _, dispatcher2, _ = _usecase(source)
    uc2.execute(datetime(2026, 11, 23, 21, 5, tzinfo=timezone.utc))  # 22:05 Paris
    assert dispatcher2.calls == []


def test_nothing_on_sunday():
    manager = uuid4()
    uc, _, dispatcher, _ = _usecase(FakeSource(projects=[_project(validators={manager})]))
    report = uc.execute(SUN_1935)
    assert report.due == [] and dispatcher.calls == []


def test_a_second_reminder_the_same_day_only_goes_to_the_bell():
    admin = uuid4()
    company = uuid4()
    log = FakeLog()
    source = FakeSource(
        projects=[_project(validators={admin}, admins={admin})],
        factures=[OverdueFacture(company, "FAC-1", Decimal("100"))],
        admins={company: {admin}},
    )
    uc, _, dispatcher, events = _usecase(source, log)
    uc.execute(MON_0905)  # the Monday overdue push
    assert len(dispatcher.calls) == 1 and events.records == []

    uc2, _, dispatcher2, events2 = _usecase(source, log)
    report = uc2.execute(MON_1935)  # same day, the attendance reminder
    assert dispatcher2.calls == []
    assert [p.action for p in report.planned] == ["bell_only_capped"]
    assert events2.records[0]["kind"] == "reminder_attendance"
    assert events2.records[0]["texts"]["vi"] == ["Chưa chấm công hôm nay", "Chantier A · 23/11"]


def test_a_muted_person_gets_the_bell_and_keeps_the_days_push():
    admin = uuid4()
    company = uuid4()
    log = FakeLog()
    prefs = FakePreferences({admin: {"billing"}})
    source = FakeSource(
        projects=[_project(validators={admin}, admins={admin})],
        factures=[OverdueFacture(company, "FAC-1", Decimal("100"))],
        admins={company: {admin}},
    )
    uc, _, dispatcher, events = _usecase(source, log, prefs)
    report = uc.execute(MON_0905)
    assert dispatcher.calls == []
    assert [p.action for p in report.planned] == ["bell_only_muted"]
    assert events.records[0]["kind"] == "reminder_overdue_factures"

    uc2, _, dispatcher2, _ = _usecase(source, log, prefs)
    uc2.execute(MON_1935)
    assert len(dispatcher2.calls) == 1  # attendance is not muted and the day's push is unused


def test_dry_run_writes_and_sends_nothing():
    manager = uuid4()
    log = FakeLog()
    uc, _, dispatcher, events = _usecase(FakeSource(projects=[_project(validators={manager})]), log)
    report = uc.execute(MON_1935, dry_run=True)
    assert [p.action for p in report.planned] == ["push"]
    assert report.planned[0].texts["vi"] == ("Chưa chấm công hôm nay", "Chantier A · 23/11")
    assert log.rows == {} and dispatcher.calls == [] and events.records == []


def test_a_claim_lost_to_another_run_sends_nothing():
    manager = uuid4()

    class LosingLog(FakeLog):
        def claim(self, *args, **kwargs):
            return False

    uc, _, dispatcher, events = _usecase(FakeSource(projects=[_project(validators={manager})]), LosingLog())
    report = uc.execute(MON_1935)
    assert dispatcher.calls == [] and events.records == []
    assert report.planned[0].action == "already_sent"


# -- overdue factures and the month summary -------------------------------------------------


def test_overdue_factures_reach_the_company_admins_with_count_and_total():
    admin_a, admin_b = uuid4(), uuid4()
    company = uuid4()
    source = FakeSource(
        factures=[
            OverdueFacture(company, "FAC-1", Decimal("1200.50")),
            OverdueFacture(company, "FAC-2", Decimal("300")),
        ],
        admins={company: {admin_a, admin_b}},
    )
    uc, _, dispatcher, _ = _usecase(source)
    uc.execute(MON_0905)
    assert {c["recipients"][0] for c in dispatcher.calls} == {admin_a, admin_b}
    call = dispatcher.calls[0]
    assert call["category"] == "billing"
    assert call["data"] == {
        "kind": "reminder_overdue_factures",
        "date": "2026-11-23",
        "count": "2",
        "company_id": str(company),
    }
    assert _plain(call["render"]("vi")) == ("Hóa đơn quá hạn", "2 hóa đơn quá hạn, tổng 1 500,50 €")
    assert _plain(call["render"]("fr")) == ("Factures en retard", "2 factures en retard, total 1 500,50 €")
    assert _plain(call["render"]("en")) == ("Overdue invoices", "2 overdue invoices, €1,500.50 in total")


def test_no_overdue_push_when_nothing_is_overdue():
    uc, _, dispatcher, _ = _usecase(FakeSource(admins={uuid4(): {uuid4()}}))
    uc.execute(MON_0905)
    assert dispatcher.calls == []


def test_month_summary_for_the_month_just_ended():
    admin = uuid4()
    company, quiet = uuid4(), uuid4()
    source = FakeSource(
        admins={company: {admin}, quiet: {uuid4()}},
        labor={
            company: [
                WorkerMonth("An", Decimal("10"), Decimal("1500"), Decimal("1500")),
                WorkerMonth("Bình", Decimal("5.5"), Decimal("825"), Decimal("0")),
            ],
            quiet: [],  # nothing logged, nothing owed: no summary
        },
    )
    uc, _, dispatcher, _ = _usecase(source)
    uc.execute(FIRST_TUE_0905)
    assert [c["recipients"] for c in dispatcher.calls] == [[admin]]
    assert ("month", company, 2026, 11) in source.asked
    call = dispatcher.calls[0]
    assert call["data"]["month"] == "2026-11"
    assert _plain(call["render"]("vi")) == (
        "Tổng kết tháng 11/2026",
        "15,5 ngày công · lương 2 325,00 € · còn nợ 825,00 € (1 người)",
    )
    assert (
        _plain(call["render"]("fr"))[1]
        == "15,5 jours pointés · salaires 2 325,00 € · reste à payer 825,00 € (1 ouvrier)"
    )
    assert _plain(call["render"]("en")) == (
        "November 2026 summary",
        "15.5 days logged · wages €2,325.00 · €825.00 still owed (1 worker)",
    )


def test_month_summary_and_overdue_on_the_same_morning_are_one_push():
    admin = uuid4()
    company = uuid4()
    log = FakeLog()
    source = FakeSource(
        factures=[OverdueFacture(company, "FAC-1", Decimal("100"))],
        admins={company: {admin}},
        labor={company: [WorkerMonth("An", Decimal("2"), Decimal("300"), Decimal("300"))]},
    )
    uc, _, dispatcher, _ = _usecase(source, log)
    report = uc.execute(FIRST_MON_0905)
    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call["data"]["kind"] == "reminder_monthly_summary"
    assert call["data"]["overdue_count"] == "1"
    assert (
        _plain(call["render"]("vi"))[1]
        == "2 ngày công · lương 300,00 € · đã trả đủ lương · 1 hóa đơn quá hạn (100,00 €)"
    )
    assert sorted(report.planned[0].kinds) == ["reminder_monthly_summary", "reminder_overdue_factures"]
    # Both are recorded: neither comes back on a later run that day.
    uc2, _, dispatcher2, _ = _usecase(source, log)
    uc2.execute(datetime(2026, 6, 1, 9, 5, tzinfo=timezone.utc))
    assert dispatcher2.calls == []


def test_month_figures_merge_a_person_across_sites_and_months_stand_alone():
    fig = month_figures(
        [
            WorkerMonth("Trần  Văn An", Decimal("3"), Decimal("450"), Decimal("0")),
            WorkerMonth("trần văn an", Decimal("2"), Decimal("300"), Decimal("1000")),  # overpaid on site B
            WorkerMonth("Bình", Decimal("1"), Decimal("150"), Decimal("50")),
        ]
    )
    assert fig.days == Decimal("6")
    assert fig.earned == Decimal("900.00")
    # An: 750 earned, 1000 paid -> owes nothing (never negative); Bình owes 100.
    assert fig.owed == Decimal("100.00")
    assert fig.owed_workers == 1


# -- through the real dispatcher -----------------------------------------------------------------


class _Sender:
    def __init__(self):
        self.sent = []

    def send(self, messages, on_invalid_token=None):
        self.sent.extend(messages)


class _Devices:
    def __init__(self, tokens, locales):
        self._tokens = tokens
        self._locales = locales

    def tokens_for_users(self, user_ids):
        return {u: self._tokens[u] for u in user_ids if u in self._tokens}

    def locales_for_tokens(self, tokens):
        return {t: self._locales[t] for t in tokens if t in self._locales}

    def delete_token(self, token):
        pass


def test_the_push_is_rendered_per_device_language_and_lands_in_the_bell():
    manager = uuid4()
    sender = _Sender()
    events = FakeEvents()
    dispatcher = PushDispatcher(
        devices=_Devices({manager: ["tok-vi", "tok-fr"]}, {"tok-vi": "vi", "tok-fr": "fr"}),
        sender=sender,
        run_async=False,
        events=events,
    )
    uc = SendScheduledRemindersUseCase(
        FakeSource(projects=[_project("Nhà Lan", validators={manager})]), FakeLog(), dispatcher, events=events
    )
    uc.execute(MON_1935)
    texts = {m.token: (m.title, m.body) for m in sender.sent}
    assert texts == {
        "tok-vi": ("Chưa chấm công hôm nay", "Nhà Lan · 23/11"),
        "tok-fr": ("Pointage du jour manquant", "Nhà Lan · 23/11"),
    }
    assert sender.sent[0].data["kind"] == "reminder_attendance"
    assert events.records[0]["kind"] == "reminder_attendance"
    assert events.records[0]["category"] == "attendance"
