"""Verified change of the sign-in phone (app/application/usecases/change_phone.py).

The code is texted to the NEW number, bound to the requesting user and stored with the
PHONE_CHANGE purpose: it must never sign anyone in, and a sign-in code must never change a number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.usecases.change_phone import (
    PHONE_CHANGE_MESSAGE,
    ConfirmPhoneChangeUseCase,
    RequestPhoneChangeCodeUseCase,
)
from app.application.usecases.otp_login import _consume_code, _issue_code
from app.domain.entities.login_otp import OtpPurpose
from app.domain.exceptions.auth_exceptions import (
    OtpInvalidError,
    OtpThrottledError,
    PhoneAlreadyRegisteredError,
    PhoneUnchangedError,
    UserInactiveError,
)
from app.domain.value_objects.phone_number import InvalidPhoneNumberError
from tests.unit.application.test_otp_reviewer_account import FakeOtps, FakeSms

OLD = "+33611111111"
NEW = "+33622222222"
NOW = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)


class FakeUsers:
    def __init__(self, *users) -> None:
        self.users = {u.id: u for u in users}
        self.saved: list = []

    def find_by_id(self, user_id):
        return self.users.get(user_id)

    def find_by_phone(self, phone):
        return next((u for u in self.users.values() if u.phone == phone), None)

    def save(self, user):
        self.saved.append(user)
        return user


def _user(phone=OLD, active=True):
    return SimpleNamespace(id=uuid4(), phone=phone, is_active=active)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in ("OTP_REVIEWER_PHONE", "OTP_REVIEWER_CODE", "OTP_TEST_CODE", "FLASK_ENV"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def env():
    me = _user()
    users, otps, sms, clock = FakeUsers(me), FakeOtps(), FakeSms(), Clock()
    request = RequestPhoneChangeCodeUseCase(users, otps, sms, clock=clock)
    confirm = ConfirmPhoneChangeUseCase(users, otps, clock=clock)
    return SimpleNamespace(me=me, users=users, otps=otps, sms=sms, clock=clock, request=request, confirm=confirm)


def _last_code(sms) -> str:
    return sms.sent[-1][1].split(" la ")[1][:6]


def test_code_is_texted_to_the_new_number_and_confirm_switches(env):
    result = env.request.execute(env.me.id, "06 22 22 22 22")
    assert result.expires_in == 300
    assert env.sms.sent[-1][0] == NEW
    assert env.sms.sent[-1][1] == PHONE_CHANGE_MESSAGE.format(code=_last_code(env.sms), minutes=5)
    stored = env.otps.rows[-1]
    assert stored.purpose is OtpPurpose.PHONE_CHANGE and stored.user_id == env.me.id

    user = env.confirm.execute(env.me.id, NEW, _last_code(env.sms))
    assert user.phone == NEW and env.users.saved == [env.me]


def test_code_is_single_use(env):
    env.request.execute(env.me.id, NEW)
    code = _last_code(env.sms)
    env.confirm.execute(env.me.id, NEW, code)
    env.me.phone = OLD  # pretend nothing changed: the code itself must still be spent
    with pytest.raises(OtpInvalidError):
        env.confirm.execute(env.me.id, NEW, code)


def test_expired_code_is_refused(env):
    env.request.execute(env.me.id, NEW)
    env.clock.now = NOW + timedelta(seconds=301)
    with pytest.raises(OtpInvalidError):
        env.confirm.execute(env.me.id, NEW, _last_code(env.sms))
    assert env.me.phone == OLD


def test_phone_change_code_is_not_a_sign_in_code(env):
    env.request.execute(env.me.id, NEW)
    with pytest.raises(OtpInvalidError):
        _consume_code(env.otps, phone=NEW, code=_last_code(env.sms), now=NOW, max_attempts=5)


def test_sign_in_code_is_not_a_phone_change_code(env):
    _issue_code(
        env.otps,
        env.sms,
        phone=NEW,
        user_id=env.me.id,
        now=NOW,
        ttl=300,
        resend_after=60,
        hourly_max=5,
        message="Folio: ma la {code} {minutes}",
    )
    with pytest.raises(OtpInvalidError):
        env.confirm.execute(env.me.id, NEW, _last_code(env.sms))
    assert env.me.phone == OLD


def test_a_new_phone_change_code_does_not_void_a_sign_in_code(env):
    _issue_code(
        env.otps,
        env.sms,
        phone=NEW,
        user_id=None,
        now=NOW,
        ttl=300,
        resend_after=0,
        hourly_max=5,
        message="Folio: ma la {code} {minutes}",
    )
    sign_in_code = _last_code(env.sms)
    env.clock.now = NOW + timedelta(seconds=61)
    env.request.execute(env.me.id, NEW)
    otp = _consume_code(env.otps, phone=NEW, code=sign_in_code, now=env.clock.now, max_attempts=5)
    assert otp.purpose is OtpPurpose.SIGN_IN


def test_number_used_by_another_account_is_refused_before_any_sms(env):
    env.users.users[uuid4()] = SimpleNamespace(id=uuid4(), phone=NEW, is_active=True)
    with pytest.raises(PhoneAlreadyRegisteredError) as exc:
        env.request.execute(env.me.id, NEW)
    assert NEW not in str(exc.value)
    assert env.sms.sent == []


def test_reviewer_number_cannot_be_taken(env, monkeypatch):
    monkeypatch.setenv("OTP_REVIEWER_PHONE", NEW)
    monkeypatch.setenv("OTP_REVIEWER_CODE", "246810")
    with pytest.raises(PhoneAlreadyRegisteredError):
        env.request.execute(env.me.id, NEW)
    with pytest.raises(PhoneAlreadyRegisteredError):
        env.confirm.execute(env.me.id, NEW, "246810")


def test_current_number_is_refused(env):
    with pytest.raises(PhoneUnchangedError):
        env.request.execute(env.me.id, "06 11 11 11 11")


@pytest.mark.parametrize("phone", ["hello", "+84912345678"])
def test_invalid_and_foreign_numbers_are_refused(env, phone):
    with pytest.raises(InvalidPhoneNumberError):
        env.request.execute(env.me.id, phone)
    assert env.sms.sent == []


def test_inactive_user_is_refused(env):
    env.me.is_active = False
    with pytest.raises(UserInactiveError):
        env.request.execute(env.me.id, NEW)


def test_same_throttle_as_sign_in(env):
    env.request.execute(env.me.id, NEW)
    with pytest.raises(OtpThrottledError):
        env.request.execute(env.me.id, NEW)
    for minute in range(1, 5):
        env.clock.now = NOW + timedelta(minutes=minute * 2)
        env.request.execute(env.me.id, NEW)
    env.clock.now = NOW + timedelta(minutes=12)
    with pytest.raises(OtpThrottledError):
        env.request.execute(env.me.id, NEW)  # sixth code within the hour


def test_attempts_are_limited(env):
    env.request.execute(env.me.id, NEW)
    code = _last_code(env.sms)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(OtpInvalidError):
            env.confirm.execute(env.me.id, NEW, wrong)
    with pytest.raises(OtpInvalidError):
        env.confirm.execute(env.me.id, NEW, code)
    assert env.me.phone == OLD


def test_code_requested_by_another_user_does_not_work(env):
    other = _user(phone="+33633333333")
    env.users.users[other.id] = other
    env.request.execute(other.id, NEW)
    with pytest.raises(OtpInvalidError):
        env.confirm.execute(env.me.id, NEW, _last_code(env.sms))
    assert env.me.phone == OLD
