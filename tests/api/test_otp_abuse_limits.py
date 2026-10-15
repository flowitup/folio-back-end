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
        assert _invite(limited_app, ip, "+33611000013").status_code != 429
        for route in (SIGNUP, SIGN_IN):
            blocked = _post(limited_app, route, ip, "+33611000014")
            assert blocked.status_code == 429
        assert _invite(limited_app, ip, "+33611000015").status_code == 429

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


@pytest.fixture(scope="module")
def _budget_app():
    """An app whose whole SMS budget is 2 per 10 minutes, to watch what happens past it."""
    saved_container = _wiring.container

    class BudgetConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        SMS_GLOBAL_MAX_PER_10MIN = 2

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
    container = _wiring.get_container()
    assert container.sms_budget is not None and container.sms_sender is not None
    container.sms_budget._sent.clear()
    return container.sms_budget


class TestGlobalSmsBudget:
    def test_texts_past_the_budget_get_the_throttling_429(self, budget_app, budget):
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
        budget._sent.clear()
        assert client.post(SIGNUP, json={"phone": "+33611000203"}).status_code == 202

    def test_the_budget_is_shared_by_every_flow(self, budget_app, budget):
        client = budget_app.test_client()
        assert client.post(SIGNUP, json={"phone": "+33611000211"}).status_code == 202
        assert client.post(SIGNUP, json={"phone": "+33611000212"}).status_code == 202
        invite = client.post(INVITE_CODE, json={"token": "x" * 40, "phone": "+33611000213"})
        assert invite.status_code != 202  # unknown invitation, and no SMS for it either way
        assert client.post(SIGNUP, json={"phone": "+33611000214"}).status_code == 429

    def test_store_review_numbers_send_nothing_so_neither_spend_nor_wait(self, budget_app, budget, monkeypatch):
        from uuid import uuid4

        from app.infrastructure.database.models import UserModel

        reviewer = "+33611000220"
        monkeypatch.setenv("OTP_REVIEWER_PHONE", reviewer)
        monkeypatch.setenv("OTP_REVIEWER_CODE", "246810")
        db.session.add(UserModel(email=f"{uuid4().hex}@example.com", is_active=True, phone=reviewer))
        db.session.commit()
        client = budget_app.test_client()
        for n in (1, 2):
            assert client.post(SIGNUP, json={"phone": f"+3361100023{n}"}).status_code == 202
        assert client.post(SIGNUP, json={"phone": "+33611000233"}).status_code == 429  # the budget is spent
        spent = len(budget._sent)

        assert client.post(SIGN_IN, json={"phone": reviewer}).status_code == 202
        assert len(budget._sent) == spent
