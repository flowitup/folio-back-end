"""Security tests for the store-review account OTP bypass.

`_reviewer_code_for` (otp_login.py) gives ONE configured phone number a fixed sign-in
code so App Store / Play reviewers can log in without an SMS. It runs in production,
so it must stay narrowly scoped: only the configured number, only when both env vars
are set, never accepted for any other phone, and a broken configuration disables it
rather than widening it. `_issue_code` must skip the SMS (and throttle) for that
number only; `_consume_code` must accept the fixed code for that number only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.usecases.otp_login import _consume_code, _issue_code, _reviewer_code_for
from app.domain.entities.login_otp import LoginOtp
from app.domain.exceptions.auth_exceptions import OtpInvalidError, OtpThrottledError

REVIEWER = "+33600000000"
OTHER = "+33612345678"
CODE = "246810"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class FakeOtps:
    def __init__(self) -> None:
        self.rows: list[LoginOtp] = []

    def save(self, otp: LoginOtp) -> None:
        self.rows = [r for r in self.rows if r.id != otp.id] + [otp]

    def latest_for_phone(self, phone: str):
        rows = [r for r in self.rows if r.phone == phone]
        return max(rows, key=lambda r: r.created_at) if rows else None

    def count_created_since(self, phone: str, since: datetime) -> int:
        return sum(1 for r in self.rows if r.phone == phone and r.created_at >= since)

    def void_active(self, phone: str, now: datetime) -> None:
        for r in self.rows:
            if r.phone == phone and r.is_active(now):
                r.consumed_at = now


class FakeSms:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, phone: str, message: str) -> None:
        self.sent.append((phone, message))


def _issue(otps, sms, phone, now=NOW):
    _issue_code(
        otps,
        sms,
        phone=phone,
        user_id=uuid4(),
        now=now,
        ttl=300,
        resend_after=60,
        hourly_max=5,
        message="{code} {minutes}",
    )


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.delenv("OTP_REVIEWER_PHONE", raising=False)
    monkeypatch.delenv("OTP_REVIEWER_CODE", raising=False)
    monkeypatch.delenv("OTP_TEST_CODE", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)


class TestReviewerCodeFor:
    def test_disabled_when_nothing_configured(self):
        assert _reviewer_code_for(REVIEWER) is None

    @pytest.mark.parametrize("phone_env", ["", "   "])
    def test_disabled_when_phone_missing(self, monkeypatch, phone_env):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", phone_env)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) is None

    def test_disabled_when_code_missing(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", "")
        assert _reviewer_code_for(REVIEWER) is None

    def test_disabled_when_phone_is_not_a_french_number(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", "+84912345678")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for("+84912345678") is None

    @pytest.mark.parametrize("phone_env", [REVIEWER, "06 00 00 00 00", "0033600000000"])
    def test_matches_the_normalised_reviewer_number_only(self, monkeypatch, phone_env):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", phone_env)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) == CODE
        assert _reviewer_code_for(OTHER) is None


class TestConsumeCode:
    def _seed(self, otps, phone):
        otps.save(
            LoginOtp(
                id=uuid4(),
                user_id=uuid4(),
                phone=phone,
                code_hash="x",
                expires_at=NOW + timedelta(minutes=5),
                created_at=NOW,
            )
        )

    def test_fixed_code_signs_the_reviewer_in(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps = FakeOtps()
        self._seed(otps, REVIEWER)
        otp = _consume_code(otps, phone=REVIEWER, code=CODE, now=NOW, max_attempts=5)
        assert otp.consumed_at == NOW

    def test_fixed_code_is_rejected_for_any_other_phone(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps = FakeOtps()
        self._seed(otps, OTHER)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=OTHER, code=CODE, now=NOW, max_attempts=5)
        assert otps.latest_for_phone(OTHER).attempts == 1

    def test_wrong_code_still_fails_for_the_reviewer(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps = FakeOtps()
        self._seed(otps, REVIEWER)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=REVIEWER, code="000000", now=NOW, max_attempts=5)

    def test_fixed_code_is_rejected_when_not_configured(self):
        otps = FakeOtps()
        self._seed(otps, REVIEWER)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=REVIEWER, code=CODE, now=NOW, max_attempts=5)

    def test_reviewer_still_needs_a_requested_code(self, monkeypatch):
        """No /otp/request first → nothing active → refused, like everyone else."""
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        with pytest.raises(OtpInvalidError):
            _consume_code(FakeOtps(), phone=REVIEWER, code=CODE, now=NOW, max_attempts=5)


class TestIssueCode:
    def test_reviewer_gets_no_sms_and_no_throttle(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps, sms = FakeOtps(), FakeSms()
        for i in range(7):  # beyond hourly_max=5 and inside resend_after=60s
            _issue(otps, sms, REVIEWER, now=NOW + timedelta(seconds=i))
        assert sms.sent == []
        assert otps.latest_for_phone(REVIEWER) is not None

    def test_other_phones_are_unaffected(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps, sms = FakeOtps(), FakeSms()
        _issue(otps, sms, OTHER)
        assert len(sms.sent) == 1 and sms.sent[0][0] == OTHER
        with pytest.raises(OtpThrottledError):
            _issue(otps, sms, OTHER, now=NOW + timedelta(seconds=10))

    def test_reviewer_number_behaves_normally_when_not_configured(self):
        otps, sms = FakeOtps(), FakeSms()
        _issue(otps, sms, REVIEWER)
        assert len(sms.sent) == 1
