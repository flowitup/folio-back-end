"""Security tests for the store-review account OTP bypass.

`_reviewer_code_for` (otp_login.py) gives ONE configured phone number a fixed sign-in
code so App Store / Play reviewers can log in without an SMS. It runs in production,
so it must stay narrowly scoped: only the configured number, only when both env vars
are set, never accepted for any other phone, and a broken configuration disables it
rather than widening it. `_issue_code` must skip the SMS (and throttle) for that
number only (and keeps a short throttle on it); `_consume_code` must accept the fixed code for that
number only. Wrong guesses are budgeted across re-issued codes for reviewer numbers only: ordinary numbers keep
the per-code attempts, so a stranger cannot lock the owner out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.usecases.otp_login import (
    _hash_code,
    FAILED_GUESS_BUDGET_PER_HOUR,
    REVIEWER_HOURLY_MAX,
    _consume_code,
    _issue_code,
    _reviewer_code_for,
    is_reviewer_phone,
)
from app.domain.entities.login_otp import LoginOtp, OtpPurpose
from app.domain.exceptions.auth_exceptions import OtpInvalidError, OtpThrottledError

REVIEWER = "+33600000000"
REVIEWER_2 = "+33600000001"
OTHER = "+33612345678"
CODE = "246810"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class FakeOtps:
    def __init__(self) -> None:
        self.rows: list[LoginOtp] = []
        self.locked_reads: list[str] = []

    def save(self, otp: LoginOtp) -> None:
        self.rows = [r for r in self.rows if r.id != otp.id] + [otp]

    def latest_for_phone(self, phone: str, purpose=None, *, for_update=False):
        if for_update:
            self.locked_reads.append(phone)
        rows = [r for r in self.rows if r.phone == phone and (purpose is None or r.purpose == purpose)]
        return max(rows, key=lambda r: r.created_at) if rows else None

    def count_created_since(self, phone: str, since: datetime) -> int:
        return sum(1 for r in self.rows if r.phone == phone and r.created_at >= since)

    def sum_attempts_since(self, phone: str, since: datetime) -> int:
        return sum(r.attempts for r in self.rows if r.phone == phone and r.created_at >= since)

    def oldest_created_since(self, phone: str, since: datetime):
        created = [r.created_at for r in self.rows if r.phone == phone and r.created_at >= since]
        return min(created) if created else None

    def purge_stale(self, phone: str, before: datetime, batch_size: int) -> int:
        return 0

    def void_active(self, phone: str, now: datetime, purpose=OtpPurpose.SIGN_IN) -> None:
        for r in self.rows:
            if r.phone == phone and r.purpose == purpose and r.is_active(now):
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

    def test_comma_separated_list_covers_each_number_only(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f"{REVIEWER}, 06 00 00 00 01")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) == CODE
        assert _reviewer_code_for("+33600000001") == CODE
        assert _reviewer_code_for(OTHER) is None

    @pytest.mark.parametrize(
        "phone_env",
        [
            f"{REVIEWER},{REVIEWER_2},",
            f",{REVIEWER},{REVIEWER_2}",
            f"{REVIEWER},,{REVIEWER_2}",
            f"  {REVIEWER} ,\t{REVIEWER_2}  ,  ",
            f"{REVIEWER} , , {REVIEWER_2}",
        ],
    )
    def test_stray_separators_and_spaces_do_not_disable_either_number(self, monkeypatch, phone_env):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", phone_env)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) == CODE
        assert _reviewer_code_for(REVIEWER_2) == CODE
        assert _reviewer_code_for(OTHER) is None
        assert is_reviewer_phone(REVIEWER_2) and not is_reviewer_phone(OTHER)

    @pytest.mark.parametrize("phone_env", [",", " , ,,"])
    def test_a_list_of_only_separators_grants_nothing(self, monkeypatch, phone_env):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", phone_env)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) is None and _reviewer_code_for(OTHER) is None

    def test_list_with_a_malformed_entry_disables_the_bypass(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f"{REVIEWER},+84912345678")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        assert _reviewer_code_for(REVIEWER) is None


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

    def test_code_is_read_under_a_row_lock(self):
        """Parallel guesses must queue on the code's row, or each one writes back the same counter."""
        otps = FakeOtps()
        self._seed(otps, OTHER)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=OTHER, code="000000", now=NOW, max_attempts=5)
        assert otps.locked_reads == [OTHER]

    @pytest.mark.parametrize("code", ["\u0661\u0662\u0663\u0664\u0665\u0666", "24681\uff10"])
    def test_non_ascii_code_is_refused_not_a_server_error(self, monkeypatch, code):
        """compare_digest raises TypeError on a non-ASCII str: that used to be a 500 for the reviewer number."""
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps = FakeOtps()
        self._seed(otps, REVIEWER)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=REVIEWER, code=code, now=NOW, max_attempts=5)

    def test_reviewer_still_needs_a_requested_code(self, monkeypatch):
        """No /otp/request first → nothing active → refused, like everyone else."""
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        with pytest.raises(OtpInvalidError):
            _consume_code(FakeOtps(), phone=REVIEWER, code=CODE, now=NOW, max_attempts=5)


class TestIssueCode:
    def test_reviewer_gets_no_sms(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", REVIEWER)
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps, sms = FakeOtps(), FakeSms()
        for i in range(7):  # beyond hourly_max=5, spaced past the reviewer's short gap
            _issue(otps, sms, REVIEWER, now=NOW + timedelta(seconds=6 * i))
        assert sms.sent == []
        assert otps.latest_for_phone(REVIEWER) is not None

    def test_reviewer_has_a_short_resend_gap(self, monkeypatch):
        """Re-issuing resets the per-code guesses, so it is throttled even though no SMS goes out."""
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f"{REVIEWER},{REVIEWER_2}")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        for phone in (REVIEWER, REVIEWER_2):
            otps, sms = FakeOtps(), FakeSms()
            _issue(otps, sms, phone)
            with pytest.raises(OtpThrottledError) as exc:
                _issue(otps, sms, phone, now=NOW + timedelta(seconds=2))
            assert exc.value.retry_after_seconds == 3 and exc.value.hourly_limit is False
            _issue(otps, sms, phone, now=NOW + timedelta(seconds=5))  # the gap is 5 s, not a minute

    def test_reviewer_hourly_cap_is_generous_but_finite(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f"{REVIEWER},{REVIEWER_2}")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        for phone in (REVIEWER, REVIEWER_2):
            otps, sms = FakeOtps(), FakeSms()
            for i in range(REVIEWER_HOURLY_MAX):  # well past the regular hourly_max=5
                _issue(otps, sms, phone, now=NOW + timedelta(seconds=10 * i))
            with pytest.raises(OtpThrottledError) as exc:
                _issue(otps, sms, phone, now=NOW + timedelta(seconds=10 * REVIEWER_HOURLY_MAX))
            assert exc.value.hourly_limit is True
            _issue(otps, sms, phone, now=NOW + timedelta(hours=1, seconds=1))  # the hour moved on
            assert sms.sent == []

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


def _burn_one_code(otps, sms, phone, at, *, code="000000"):
    """Request a code, then spend all of its 5 guesses on wrong ones; return how many were refused as wrong."""
    _issue(otps, sms, phone, now=at)
    wrong = 0
    for _ in range(5):
        try:
            _consume_code(otps, phone=phone, code=code, now=at, max_attempts=5)
        except OtpInvalidError:
            wrong += 1
    return wrong


class TestFailedGuessBudget:
    """Re-requesting a code resets its 5 guesses; the phone's total of the last hour must not reset."""

    @pytest.fixture
    def reviewers(self, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f"{REVIEWER},{REVIEWER_2}")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)

    @pytest.mark.parametrize("phone", [REVIEWER, REVIEWER_2])
    def test_reviewer_numbers_cannot_buy_fresh_guesses_by_re_requesting(self, reviewers, phone):
        otps, sms = FakeOtps(), FakeSms()
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5):
            _burn_one_code(otps, sms, phone, NOW + timedelta(seconds=6 * i))
        assert sum(r.attempts for r in otps.rows) == FAILED_GUESS_BUDGET_PER_HOUR
        # A fresh code, but the budget is spent: even the right fixed code is refused, and not counted.
        at = NOW + timedelta(seconds=60)
        _issue(otps, sms, phone, now=at)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=phone, code=CODE, now=at, max_attempts=5)
        assert otps.latest_for_phone(phone).attempts == 0 and otps.latest_for_phone(phone).consumed_at is None

    @pytest.mark.parametrize("phone", [REVIEWER, REVIEWER_2])
    def test_budget_covers_every_number_of_a_list_with_a_stray_comma(self, monkeypatch, phone):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", f" {REVIEWER}, {REVIEWER_2},")
        monkeypatch.setenv("OTP_REVIEWER_CODE", CODE)
        otps, sms = FakeOtps(), FakeSms()
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5):
            _burn_one_code(otps, sms, phone, NOW + timedelta(seconds=6 * i))
        at = NOW + timedelta(seconds=60)
        _issue(otps, sms, phone, now=at)
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=phone, code=CODE, now=at, max_attempts=5)

    def test_budget_is_per_phone(self, reviewers):
        otps, sms = FakeOtps(), FakeSms()
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5):
            _burn_one_code(otps, sms, REVIEWER, NOW + timedelta(seconds=6 * i))
        at = NOW + timedelta(seconds=60)
        _issue(otps, sms, REVIEWER_2, now=at)
        assert _consume_code(otps, phone=REVIEWER_2, code=CODE, now=at, max_attempts=5).consumed_at == at

    def test_the_right_code_still_works_below_the_budget(self, reviewers):
        otps, sms = FakeOtps(), FakeSms()
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5 - 1):
            _burn_one_code(otps, sms, REVIEWER, NOW + timedelta(seconds=6 * i))
        at = NOW + timedelta(seconds=60)
        _issue(otps, sms, REVIEWER, now=at)
        for _ in range(4):  # 19 wrong guesses in the hour: one under the budget
            with pytest.raises(OtpInvalidError):
                _consume_code(otps, phone=REVIEWER, code="000000", now=at, max_attempts=5)
        assert _consume_code(otps, phone=REVIEWER, code=CODE, now=at, max_attempts=5).consumed_at == at

    def test_budget_lifts_when_the_codes_leave_the_hour(self, reviewers):
        otps, sms = FakeOtps(), FakeSms()
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5):
            _burn_one_code(otps, sms, REVIEWER, NOW + timedelta(seconds=6 * i))
        later = NOW + timedelta(hours=1, minutes=1)
        _issue(otps, sms, REVIEWER, now=later)
        assert _consume_code(otps, phone=REVIEWER, code=CODE, now=later, max_attempts=5).consumed_at == later

    def test_ordinary_numbers_are_not_locked_out_by_other_callers_guesses(self):
        """A stranger's wrong guesses must not refuse the owner's real code (per-code attempts still apply)."""
        otps, sms = FakeOtps(), FakeSms()
        assert _burn_one_code(otps, sms, OTHER, NOW) == 5  # the per-code cap is unchanged
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=OTHER, code="000000", now=NOW, max_attempts=5)
        assert otps.latest_for_phone(OTHER).attempts == 5
        # Far past the cross-code budget (as if re-issued by other means), the real code still works.
        for i in range(FAILED_GUESS_BUDGET_PER_HOUR // 5 + 2):
            otps.save(
                LoginOtp(
                    id=uuid4(),
                    user_id=None,
                    phone=OTHER,
                    code_hash="x",
                    expires_at=NOW + timedelta(minutes=5),
                    created_at=NOW + timedelta(seconds=i),
                    attempts=5,
                )
            )
        at = NOW + timedelta(minutes=2)
        real = LoginOtp(
            id=uuid4(),
            user_id=None,
            phone=OTHER,
            code_hash=_hash_code(OTHER, "135790"),
            expires_at=at + timedelta(minutes=5),
            created_at=at,
        )
        otps.save(real)
        assert _consume_code(otps, phone=OTHER, code="135790", now=at, max_attempts=5).consumed_at == at
        assert otps.locked_reads  # still read under the row lock

    def test_budget_counts_every_purpose(self, reviewers):
        otps = FakeOtps()
        for purpose in (OtpPurpose.SIGN_IN, OtpPurpose.PHONE_CHANGE):
            for i in range(2):
                otps.save(
                    LoginOtp(
                        id=uuid4(),
                        user_id=None,
                        phone=REVIEWER,
                        code_hash="x",
                        expires_at=NOW + timedelta(minutes=5),
                        created_at=NOW - timedelta(minutes=i),
                        attempts=5,
                        purpose=purpose,
                    )
                )
        otps.save(
            LoginOtp(
                id=uuid4(),
                user_id=None,
                phone=REVIEWER,
                code_hash=_hash_code(REVIEWER, CODE),
                expires_at=NOW + timedelta(minutes=5),
                created_at=NOW + timedelta(seconds=1),
            )
        )
        with pytest.raises(OtpInvalidError):
            _consume_code(otps, phone=REVIEWER, code=CODE, now=NOW + timedelta(seconds=2), max_attempts=5)
