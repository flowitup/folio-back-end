"""Limits that stand between an anonymous caller and the SMS gateway, with flask-limiter switched on.

Behind the Cloudflare tunnel (TRUSTED_PROXY_HOPS=1) the caller is the last X-Forwarded-For entry.
"""

from __future__ import annotations

import pytest

import wiring as _wiring
from app import create_app, db
from app.infrastructure.rate_limiter import limiter
from config import TestingConfig

PHONE_A = "+33611000001"
PHONE_B = "+33611000002"
INVITE_CODE = "/api/v1/invitations/accept/request-code"
SIGNUP = "/api/v1/auth/signup/request"
SIGN_IN = "/api/v1/auth/otp/request"


def _phone(i: int) -> str:
    return f"+3361100010{i}"


@pytest.fixture(scope="module")
def _limited_app():
    saved_container = _wiring.container

    class LimitedConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = "memory://"
        TRUSTED_PROXY_HOPS = 1

    app = create_app(LimitedConfig)
    app.wired_container = _wiring.container  # the process-wide container is the last app's: tests put theirs back
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()
    _wiring.container = saved_container


@pytest.fixture
def limited_app(_limited_app):
    _wiring.container = _limited_app.wired_container
    with _limited_app.app_context():
        limiter.reset()
    return _limited_app


def _post(app, path: str, client_ip: str, phone: str = PHONE_A):
    return app.test_client().post(
        path,
        json={"phone": phone},
        headers={"X-Forwarded-For": client_ip},
        environ_base={"REMOTE_ADDR": "10.1.0.9"},
    )


class TestIpv6RateLimitBuckets:
    def test_addresses_of_one_64_share_the_per_minute_limit(self, limited_app):
        """Rotating the low 64 bits used to give a fresh "5 per minute" bucket per request."""
        for i in range(5):
            assert _post(limited_app, SIGN_IN, f"2001:db8:1:2::{i + 1}", _phone(i)).status_code == 202
        assert _post(limited_app, SIGN_IN, "2001:db8:1:2:aaaa:bbbb:cccc:dddd", _phone(5)).status_code == 429

    def test_another_64_has_its_own_bucket(self, limited_app):
        for i in range(5):
            _post(limited_app, SIGN_IN, f"2001:db8:1:2::{i + 1}", _phone(i))
        assert _post(limited_app, SIGN_IN, "2001:db8:1:3::1", _phone(5)).status_code == 202

    def test_ipv4_mapped_address_is_the_ipv4(self, limited_app):
        for i in range(5):
            _post(limited_app, SIGN_IN, "203.0.113.7", _phone(i))
        assert _post(limited_app, SIGN_IN, "::ffff:203.0.113.7", _phone(5)).status_code == 429
        assert _post(limited_app, SIGN_IN, "203.0.113.8", _phone(5)).status_code == 202

    def test_a_forged_left_hand_forwarded_entry_does_not_pick_the_bucket(self, limited_app):
        """Only the entry the trusted proxy appended counts: the client cannot name its own bucket."""
        for i in range(5):
            _post(limited_app, SIGN_IN, f"198.51.100.{i + 1}, 203.0.113.7", _phone(i))
        assert _post(limited_app, SIGN_IN, "198.51.100.99, 203.0.113.7", _phone(5)).status_code == 429


def _invite(app, client_ip: str, phone: str):
    return app.test_client().post(
        INVITE_CODE,
        json={"token": "x" * 40, "phone": phone},
        headers={"X-Forwarded-For": client_ip},
        environ_base={"REMOTE_ADDR": "10.1.0.9"},
    )


class TestSmsSendCapsPerAddress:
    """The three routes that text an unauthenticated caller share an hourly/daily cap per address."""

    @pytest.fixture(autouse=True)
    def _low_cap(self, limited_app, monkeypatch):
        monkeypatch.setitem(limited_app.config, "SMS_SEND_IP_LIMITS", "3 per hour")

    def test_the_cap_is_shared_by_the_three_routes_and_counts_distinct_numbers(self, limited_app):
        ip = "203.0.113.50"
        assert _post(limited_app, SIGNUP, ip, "+33611000011").status_code == 202
        assert _post(limited_app, SIGN_IN, ip, "+33611000012").status_code == 202
        assert _post(limited_app, SIGNUP, ip, "+33611000013").status_code == 202
        for route in (SIGNUP, SIGN_IN):
            blocked = _post(limited_app, route, ip, "+33611000014")
            assert blocked.status_code == 429
        assert _invite(limited_app, ip, "+33611000015").status_code == 429

    def test_requests_that_send_no_code_do_not_spend_the_cap(self, limited_app, monkeypatch):
        """A bad number, or an invitation that does not exist, used to eat the hour's allowance."""
        monkeypatch.setitem(limited_app.config, "SMS_SEND_IP_LIMITS", "2 per hour")
        ip = "203.0.113.55"
        assert _post(limited_app, SIGNUP, ip, "not-a-number").status_code == 400
        assert _invite(limited_app, ip, "+33611000041").status_code == 404
        assert _post(limited_app, SIGNUP, ip, "+33611000042").status_code == 202
        assert _post(limited_app, SIGNUP, ip, "+33611000043").status_code == 202
        assert _post(limited_app, SIGNUP, ip, "+33611000044").status_code == 429

    def test_another_address_is_not_affected(self, limited_app):
        for i in range(3):
            _post(limited_app, SIGNUP, "203.0.113.51", f"+3361100002{i}")
        assert _post(limited_app, SIGNUP, "203.0.113.51", "+33611000029").status_code == 429
        assert _post(limited_app, SIGNUP, "203.0.113.52", "+33611000029").status_code == 202

    def test_addresses_of_one_ipv6_64_spend_one_cap(self, limited_app):
        for i in range(3):
            assert _post(limited_app, SIGNUP, f"2001:db8:7:7::{i + 1}", f"+3361100003{i}").status_code == 202
        assert _post(limited_app, SIGNUP, "2001:db8:7:7:f::1", "+33611000039").status_code == 429
        assert _post(limited_app, SIGNUP, "2001:db8:7:8::1", "+33611000039").status_code == 202

    def test_the_default_is_20_an_hour_and_60_a_day(self, limited_app, monkeypatch):
        monkeypatch.undo()
        from app.infrastructure.rate_limiter import DEFAULT_SMS_SEND_IP_LIMITS

        assert DEFAULT_SMS_SEND_IP_LIMITS == "20 per hour;60 per day"
        assert limited_app.config["SMS_SEND_IP_LIMITS"] == DEFAULT_SMS_SEND_IP_LIMITS


class TestLimiterAnswersTellTheClientHowLongToWait:
    """The hourly caps answer like the use cases' own hourly cap: ``OtpHourlyLimit`` + Retry-After."""

    def test_the_per_phone_hourly_cap(self, limited_app):
        ip = "203.0.113.70"
        for _ in range(4):
            _post(limited_app, SIGN_IN, ip, PHONE_A)
        blocked = _post(limited_app, SIGN_IN, ip, PHONE_A)
        assert blocked.status_code == 429
        assert blocked.get_json()["error"] == "OtpHourlyLimit"
        assert 3500 < int(blocked.headers["Retry-After"]) <= 3600

    def test_the_shared_address_cap(self, limited_app, monkeypatch):
        monkeypatch.setitem(limited_app.config, "SMS_SEND_IP_LIMITS", "1 per hour")
        ip = "203.0.113.71"
        assert _post(limited_app, SIGNUP, ip, "+33611000051").status_code == 202
        blocked = _post(limited_app, SIGNUP, ip, "+33611000052")
        assert blocked.status_code == 429
        assert blocked.get_json()["error"] == "OtpHourlyLimit"
        assert 3500 < int(blocked.headers["Retry-After"]) <= 3600

    def test_the_per_minute_limit_keeps_the_short_wait(self, limited_app):
        ip = "203.0.113.72"
        for i in range(5):
            _post(limited_app, SIGN_IN, ip, _phone(i))
        blocked = _post(limited_app, SIGN_IN, ip, _phone(5))
        assert blocked.status_code == 429
        assert blocked.get_json()["error"] == "TooManyRequests"
        assert 0 < int(blocked.headers["Retry-After"]) <= 60

    def test_other_routes_keep_the_default_answer(self, limited_app):
        for _ in range(5):
            limited_app.test_client().post(
                "/api/v1/auth/otp/verify", json={}, environ_base={"REMOTE_ADDR": "10.1.0.77"}
            )
        blocked = limited_app.test_client().post(
            "/api/v1/auth/otp/verify", json={}, environ_base={"REMOTE_ADDR": "10.1.0.77"}
        )
        assert blocked.status_code == 429
        assert blocked.get_json()["error"] == "TooManyRequests"


def test_the_phone_is_not_in_the_limiter_key(limited_app):
    from app.api._helpers.rate_limit_keys import phone_and_caller_key

    with limited_app.test_request_context(
        SIGN_IN, method="POST", json={"phone": "06 11 00 00 01"}, environ_base={"REMOTE_ADDR": "203.0.113.9"}
    ):
        key = phone_and_caller_key()
    with limited_app.test_request_context(
        SIGN_IN, method="POST", json={"phone": PHONE_A}, environ_base={"REMOTE_ADDR": "203.0.113.9"}
    ):
        assert phone_and_caller_key() == key  # spelling-independent and stable
    assert "611000001" not in key and key.endswith(":203.0.113.9")


class TestOnePhonePerSource:
    """A stranger cannot use up a number's whole hourly allowance from one place."""

    def test_a_source_asks_for_one_number_four_times_an_hour(self, limited_app):
        ip = "203.0.113.60"
        for _ in range(4):
            assert _post(limited_app, SIGN_IN, ip, PHONE_A).status_code == 202
        assert _post(limited_app, SIGN_IN, ip, PHONE_A).status_code == 429
        assert _post(limited_app, SIGN_IN, "203.0.113.61", PHONE_A).status_code == 202  # another source is not

    def test_another_number_is_its_own_count(self, limited_app):
        ip = "203.0.113.64"
        for _ in range(4):
            assert _post(limited_app, SIGN_IN, ip, PHONE_A).status_code == 202
        assert _post(limited_app, SIGN_IN, ip, PHONE_B).status_code == 202

    def test_a_number_spelled_another_way_is_the_same_count(self, limited_app):
        ip = "203.0.113.63"
        for spelling in (PHONE_A, "06 11 00 00 01", "0033611000001", "+33 6 11 00 00 01"):
            assert _post(limited_app, SIGN_IN, ip, spelling).status_code == 202
        assert _post(limited_app, SIGN_IN, ip, "0611000001").status_code == 429

    def test_store_review_numbers_are_exempt(self, limited_app, monkeypatch):
        monkeypatch.setenv("OTP_REVIEWER_PHONE", PHONE_A)
        monkeypatch.setenv("OTP_REVIEWER_CODE", "246810")
        for _ in range(5):
            assert _post(limited_app, SIGN_IN, "203.0.113.62", PHONE_A).status_code == 202


PHONE_CHANGE = "/api/v1/auth/me/phone/request-code"


def _signed_in_user(phone: str):
    """(user, Authorization headers) of an active account that signs in with ``phone``."""
    from uuid import uuid4

    from app.infrastructure.database.models import UserModel

    user = UserModel(email=f"{uuid4().hex}@example.com", is_active=True, phone=phone)
    db.session.add(user)
    db.session.commit()
    token = _wiring.get_container().token_issuer.create_access_token(user.id)
    return user, {"Authorization": f"Bearer {token}"}


def _change(app, headers, client_ip: str, phone: str):
    return app.test_client().post(
        PHONE_CHANGE,
        json={"phone": phone},
        headers={**headers, "X-Forwarded-For": client_ip},
        environ_base={"REMOTE_ADDR": "10.1.0.9"},
    )


class TestPhoneChangeCodeCaps:
    """The caller picks the destination number: per-address cap shared with the other code routes, 5 a day per account."""

    def test_the_per_address_cap_is_shared_with_the_other_code_routes(self, limited_app, monkeypatch):
        monkeypatch.setitem(limited_app.config, "SMS_SEND_IP_LIMITS", "2 per hour")
        _, headers = _signed_in_user("+33611000301")
        ip = "203.0.113.80"
        assert _post(limited_app, SIGNUP, ip, "+33611000302").status_code == 202
        assert _change(limited_app, headers, ip, "+33611000303").status_code == 202
        blocked = _change(limited_app, headers, ip, "+33611000304")
        assert blocked.status_code == 429
        assert _post(limited_app, SIGNUP, ip, "+33611000305").status_code == 429  # spent by the phone change too
        assert _change(limited_app, headers, "203.0.113.81", "+33611000304").status_code == 202  # another address

    def test_requests_that_send_no_code_do_not_spend_the_caps(self, limited_app, monkeypatch):
        monkeypatch.setitem(limited_app.config, "SMS_SEND_IP_LIMITS", "1 per hour")
        user, headers = _signed_in_user("+33611000310")
        ip = "203.0.113.82"
        assert _change(limited_app, headers, ip, "not-a-number").status_code == 400
        assert _change(limited_app, headers, ip, "+33611000310").status_code == 400  # already the caller's number
        assert _change(limited_app, headers, ip, "+33611000311").status_code == 202
        assert _change(limited_app, headers, ip, "+33611000312").status_code == 429

    def test_one_account_gets_five_codes_a_day_from_wherever_it_asks(self, limited_app, monkeypatch):
        import limits.storage.memory as limits_memory

        class Clock:
            offset = 0.0

            def time(self) -> float:
                return real_time() + self.offset

        real_time = limits_memory.time.time
        clock = Clock()
        monkeypatch.setattr(limits_memory, "time", clock)  # only the limiter's storage sees the shifted time
        _, headers = _signed_in_user("+33611000320")
        for n in range(5):
            assert _change(limited_app, headers, f"203.0.113.{100 + n}", f"+3361100033{n}").status_code == 202
        clock.offset = 120  # past the per-minute window, well inside the day
        refused = _change(limited_app, headers, "203.0.113.120", "+33611000339")
        assert refused.status_code == 429  # the minute window is over, so this is the daily cap
        assert refused.get_json()["error"] == "OtpHourlyLimit"  # the key the clients localise, not the default 429
        assert 1 <= int(refused.headers["Retry-After"]) <= 86400
        other, other_headers = _signed_in_user("+33611000321")
        assert _change(limited_app, other_headers, "203.0.113.121", "+33611000339").status_code == 202  # keyed by user
        clock.offset = 86400 + 120
        assert _change(limited_app, headers, "203.0.113.122", "+33611000338").status_code == 202  # the day moved on


@pytest.fixture(scope="module")
def _budget_app():
    """An app whose two SMS pools hold 2 sends per 10 minutes each, to watch what happens past them."""
    saved_container = _wiring.container

    class BudgetConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        SMS_GLOBAL_MAX_PER_10MIN = 2
        SMS_OPEN_MAX_PER_10MIN = 2

    app = create_app(BudgetConfig)
    app.wired_container = _wiring.container
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()
    _wiring.container = saved_container


@pytest.fixture
def budget_app(_budget_app):
    _wiring.container = _budget_app.wired_container
    return _budget_app


@pytest.fixture
def budget(budget_app):
    """The sign-in pool (emptied); ``open_budget`` is the sign-up/invitation/phone-change one."""
    container = _wiring.get_container()
    assert container.sms_budget is not None and container.sms_sender is not None
    container.sms_budget._sent.clear()
    container.sms_open_budget._sent.clear()
    return container.sms_budget


@pytest.fixture
def open_budget(budget):
    return _wiring.get_container().sms_open_budget


def _add_user(phone: str):
    """An active account that signs in with ``phone``; returns it."""
    from uuid import uuid4

    from app.infrastructure.database.models import UserModel

    user = UserModel(email=f"{uuid4().hex}@example.com", is_active=True, phone=phone)
    db.session.add(user)
    db.session.commit()
    return user


class TestSmsBudgetPools:
    """Sign-in codes and sign-up/invitation/phone-change codes draw from two independent pools."""

    def test_texts_past_the_open_pool_get_the_throttling_429(self, budget_app, budget, open_budget):
        client = budget_app.test_client()
        for n in (1, 2):
            assert client.post(SIGNUP, json={"phone": f"+3361100020{n}"}).status_code == 202
        refused = client.post(SIGNUP, json={"phone": "+33611000203"})
        assert refused.status_code == 429
        body = refused.get_json()
        assert body["status_code"] == 429 and body["error"] in ("TooManyRequests", "OtpHourlyLimit")
        assert 1 <= int(refused.headers["Retry-After"]) <= 600
        # Nothing was committed for the refused request (the test's open app context keeps the session
        # alive, so end it by hand as the request teardown does): it can be repeated once there is room.
        db.session.rollback()
        open_budget._sent.clear()
        assert client.post(SIGNUP, json={"phone": "+33611000203"}).status_code == 202
        assert len(budget._sent) == 0  # sign-up codes never touched the sign-in pool

    def test_an_exhausted_open_pool_does_not_block_a_sign_in_code(self, budget_app, budget, open_budget):
        """Anonymous sign-up requests to arbitrary numbers used to stop every sign-in."""
        _add_user("+33611000240")
        client = budget_app.test_client()
        for n in (1, 2):
            assert client.post(SIGNUP, json={"phone": f"+3361100024{n}"}).status_code == 202
        assert client.post(SIGNUP, json={"phone": "+33611000243"}).status_code == 429
        db.session.rollback()
        assert len(open_budget._sent) == 2
        assert client.post(SIGN_IN, json={"phone": "+33611000240"}).status_code == 202
        assert len(budget._sent) == 1

    def test_an_exhausted_sign_in_pool_still_answers_429_and_leaves_sign_up_alone(
        self, budget_app, budget, open_budget
    ):
        for n in (0, 1, 2):
            _add_user(f"+3361100025{n}")
        client = budget_app.test_client()
        for n in (0, 1):
            assert client.post(SIGN_IN, json={"phone": f"+3361100025{n}"}).status_code == 202
        refused = client.post(SIGN_IN, json={"phone": "+33611000252"})
        assert refused.status_code == 429
        assert 1 <= int(refused.headers["Retry-After"]) <= 600
        db.session.rollback()
        assert client.post(SIGNUP, json={"phone": "+33611000253"}).status_code == 202
        assert len(open_budget._sent) == 1

    def test_phone_change_codes_draw_from_the_open_pool(self, budget_app, budget, open_budget):
        user = _add_user("+33611000260")
        _add_user("+33611000261")
        token = _wiring.get_container().token_issuer.create_access_token(user.id)
        headers = {"Authorization": f"Bearer {token}"}
        client = budget_app.test_client()
        change = "/api/v1/auth/me/phone/request-code"
        for n in (2, 3):
            assert client.post(change, json={"phone": f"+3361100026{n}"}, headers=headers).status_code == 202
        assert client.post(change, json={"phone": "+33611000264"}, headers=headers).status_code == 429
        db.session.rollback()
        assert len(open_budget._sent) == 2 and len(budget._sent) == 0
        assert client.post(SIGN_IN, json={"phone": "+33611000261"}).status_code == 202  # sign-in is unaffected

    def test_invitation_sign_up_codes_draw_from_the_open_pool(self, budget_app, budget, open_budget):
        container = _wiring.get_container()
        assert container.request_invite_otp_usecase._request_signup_otp is container.request_signup_otp_usecase
        assert container.request_signup_otp_usecase._sms is container.sms_open_sender
        assert container.request_phone_change_code_usecase._sms is container.sms_open_sender
        assert container.request_otp_usecase._sms is container.sms_sender  # the invitation's existing-account branch

    def test_a_reusable_invitation_token_cannot_empty_the_sign_in_pool(self, budget_app, budget, open_budget):
        """One token texts any number the caller names, so those codes must not draw from the sign-in pool."""
        from types import SimpleNamespace

        _add_user("+33611000270")
        invite = _wiring.get_container().request_invite_otp_usecase
        invitation = SimpleNamespace(email="stranger@example.com", accept=lambda: None)
        invite._inv_repo = SimpleNamespace(find_by_token_hash=lambda _hash: invitation)
        invite._authz_reader = None
        client = budget_app.test_client()
        for n in (1, 2):
            assert client.post(INVITE_CODE, json={"token": "x" * 40, "phone": f"+3361100027{n}"}).status_code == 202
        assert client.post(INVITE_CODE, json={"token": "x" * 40, "phone": "+33611000273"}).status_code == 429
        db.session.rollback()
        assert len(open_budget._sent) == 2 and len(budget._sent) == 0
        assert client.post(SIGN_IN, json={"phone": "+33611000270"}).status_code == 202  # sign-in still works
        assert len(budget._sent) == 1

    def test_store_review_numbers_send_nothing_so_neither_spend_nor_wait(
        self, budget_app, budget, open_budget, monkeypatch
    ):
        reviewer = "+33611000220"
        monkeypatch.setenv("OTP_REVIEWER_PHONE", reviewer)
        monkeypatch.setenv("OTP_REVIEWER_CODE", "246810")
        _add_user(reviewer)
        client = budget_app.test_client()
        for n in (1, 2):
            assert client.post(SIGNUP, json={"phone": f"+3361100023{n}"}).status_code == 202
        assert client.post(SIGNUP, json={"phone": "+33611000233"}).status_code == 429  # the open pool is spent
        db.session.rollback()
        spent = len(open_budget._sent)

        assert client.post(SIGN_IN, json={"phone": reviewer}).status_code == 202
        assert len(open_budget._sent) == spent and len(budget._sent) == 0
