"""The two SMS-code limits are told apart and each says how long to wait."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.api._helpers.otp_messages import otp_throttled
from app.domain.exceptions.auth_exceptions import OtpThrottledError
from tests.unit.application.test_otp_reviewer_account import NOW, OTHER, FakeOtps, FakeSms, _issue


def test_the_resend_gap_waits_until_a_minute_after_the_last_code():
    otps, sms = FakeOtps(), FakeSms()
    _issue(otps, sms, OTHER, now=NOW)
    with pytest.raises(OtpThrottledError) as exc:
        _issue(otps, sms, OTHER, now=NOW + timedelta(seconds=20))
    assert exc.value.hourly_limit is False
    assert exc.value.retry_after_seconds == 40


def test_the_hourly_cap_waits_until_the_oldest_code_leaves_the_hour():
    otps, sms = FakeOtps(), FakeSms()
    for minute in (0, 2, 4, 6, 8):
        _issue(otps, sms, OTHER, now=NOW + timedelta(minutes=minute))
    with pytest.raises(OtpThrottledError) as exc:
        _issue(otps, sms, OTHER, now=NOW + timedelta(minutes=10))
    assert exc.value.hourly_limit is True
    assert exc.value.retry_after_seconds == 50 * 60


def test_the_messages_name_the_limit_and_the_wait():
    error, message, headers = otp_throttled(OtpThrottledError("x", retry_after_seconds=50 * 60, hourly_limit=True))
    assert (error, headers) == ("OtpHourlyLimit", {"Retry-After": "3000"})
    assert "50 minutes" in message and "Wait a minute" not in message

    error, message, headers = otp_throttled(OtpThrottledError("x", retry_after_seconds=40))
    assert (error, headers) == ("TooManyRequests", {"Retry-After": "40"})
    assert "Wait a minute" in message
