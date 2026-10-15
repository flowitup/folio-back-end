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


@pytest.fixture(scope="module")
def limited_app():
    saved_container = _wiring.container

    class LimitedConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = "memory://"
        TRUSTED_PROXY_HOPS = 1

    app = create_app(LimitedConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()
    _wiring.container = saved_container


@pytest.fixture(autouse=True)
def _fresh_limits(limited_app):
    with limited_app.app_context():
        limiter.reset()
    yield


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
            assert _post(limited_app, "/api/v1/auth/otp/request", f"2001:db8:1:2::{i + 1}").status_code == 202
        assert _post(limited_app, "/api/v1/auth/otp/request", "2001:db8:1:2:aaaa:bbbb:cccc:dddd").status_code == 429

    def test_another_64_has_its_own_bucket(self, limited_app):
        for i in range(5):
            _post(limited_app, "/api/v1/auth/otp/request", f"2001:db8:1:2::{i + 1}")
        assert _post(limited_app, "/api/v1/auth/otp/request", "2001:db8:1:3::1").status_code == 202

    def test_ipv4_mapped_address_is_the_ipv4(self, limited_app):
        for i in range(5):
            _post(limited_app, "/api/v1/auth/otp/request", "203.0.113.7")
        assert _post(limited_app, "/api/v1/auth/otp/request", "::ffff:203.0.113.7").status_code == 429
        assert _post(limited_app, "/api/v1/auth/otp/request", "203.0.113.8").status_code == 202

    def test_a_forged_left_hand_forwarded_entry_does_not_pick_the_bucket(self, limited_app):
        """Only the entry the trusted proxy appended counts: the client cannot name its own bucket."""
        for i in range(5):
            _post(limited_app, "/api/v1/auth/otp/request", f"198.51.100.{i + 1}, 203.0.113.7")
        assert _post(limited_app, "/api/v1/auth/otp/request", "198.51.100.99, 203.0.113.7").status_code == 429
