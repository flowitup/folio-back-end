"""SQLAlchemyReminderLog against SQLite: the unique key is what makes a reminder send once."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.infrastructure.adapters.sqlalchemy_scheduled_reminders import SQLAlchemyReminderLog
from app.infrastructure.database.models import UserModel

DAY = date(2026, 11, 23)


def _user(session):
    user = UserModel(email=f"reminder-{uuid4()}@log.test", is_active=True)
    session.add(user)
    session.flush()
    return user


def test_a_claim_is_recorded_once_per_user_kind_and_day(session):
    log = SQLAlchemyReminderLog(session)
    user = _user(session)
    assert log.claim(user.id, ["reminder_attendance"], DAY, pushed=True, data={"kind": "reminder_attendance"})
    assert not log.claim(user.id, ["reminder_attendance"], DAY, pushed=True, data={})
    # Another day, or another kind, is a new reminder.
    assert log.claim(user.id, ["reminder_attendance"], date(2026, 11, 24), pushed=True, data={})
    assert log.claim(user.id, ["reminder_overdue_factures"], DAY, pushed=False, data={})


def test_a_merged_claim_is_all_or_nothing(session):
    log = SQLAlchemyReminderLog(session)
    user = _user(session)
    assert log.claim(user.id, ["reminder_overdue_factures"], DAY, pushed=False, data={})
    assert not log.claim(user.id, ["reminder_monthly_summary", "reminder_overdue_factures"], DAY, pushed=True, data={})
    state = log.sent_on([user.id], DAY)[user.id]
    assert state.kinds == {"reminder_overdue_factures"}
    assert state.pushed is False


def test_sent_on_reports_kinds_and_whether_a_push_went_out(session):
    log = SQLAlchemyReminderLog(session)
    pushed, bell_only, quiet = _user(session), _user(session), _user(session)
    log.claim(pushed.id, ["reminder_monthly_summary", "reminder_overdue_factures"], DAY, pushed=True, data={})
    log.claim(bell_only.id, ["reminder_attendance"], DAY, pushed=False, data={})
    log.claim(quiet.id, ["reminder_attendance"], date(2026, 11, 20), pushed=True, data={})
    state = log.sent_on([pushed.id, bell_only.id, quiet.id], DAY)
    assert state[pushed.id].kinds == {"reminder_monthly_summary", "reminder_overdue_factures"}
    assert state[pushed.id].pushed is True
    assert state[bell_only.id].pushed is False
    assert quiet.id not in state
