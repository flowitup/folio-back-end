"""Rate-limit behaviour shared by every route: who a limit is counted for, and what a 429 says.

- Signed-in requests are counted per user, so colleagues on one office or site Wi-Fi (or one
  carrier NAT) do not use up each other's quota; anonymous requests keep the address bucket.
- A CORS preflight (OPTIONS) is never counted.
- Every 429 carries Retry-After, a ``retry_after`` field and a readable message (never the
  limiter's internal "60 per 1 minute"), and Retry-After is readable cross-origin.
"""

from __future__ import annotations

import uuid

import pytest
from flask_jwt_extended import create_access_token

import wiring as _wiring
from app import create_app, db
from app.infrastructure.rate_limiter import limiter
from config import TestingConfig

APP_VERSION = "/api/v1/app-version"  # anonymous route limited to 60 per minute


@pytest.fixture(scope="module")
def _limited_app():
    saved_container = _wiring.container

    class LimitedConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = True
        RATELIMIT_STORAGE_URI = "memory://"
        TRUSTED_PROXY_HOPS = 1

    app = create_app(LimitedConfig)
    app.wired_container = _wiring.container
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


def _token(app) -> str:
    with app.app_context():
        return create_access_token(identity=str(uuid.uuid4()))


def _get(app, ip: str, token: str | None = None, method: str = "GET"):
    headers = {"X-Forwarded-For": ip}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method == "OPTIONS":
        headers["Origin"] = "http://localhost:3000"
        headers["Access-Control-Request-Method"] = "GET"
    return app.test_client().open(APP_VERSION, method=method, headers=headers, environ_base={"REMOTE_ADDR": "10.1.0.9"})


def _exhaust(app, ip: str, token: str | None = None) -> None:
    for _ in range(60):
        assert _get(app, ip, token).status_code == 200


class TestSignedInRequestsAreCountedPerUser:
    def test_a_colleague_on_the_same_network_keeps_their_quota(self, limited_app):
        ip = "198.51.100.10"
        alice, bob = _token(limited_app), _token(limited_app)
        _exhaust(limited_app, ip, alice)
        assert _get(limited_app, ip, alice).status_code == 429
        assert _get(limited_app, ip, bob).status_code == 200

    def test_the_same_user_on_another_network_shares_their_quota(self, limited_app):
        alice = _token(limited_app)
        _exhaust(limited_app, "198.51.100.11", alice)
        assert _get(limited_app, "198.51.100.12", alice).status_code == 429

    def test_anonymous_requests_still_share_the_address_bucket(self, limited_app):
        ip = "198.51.100.13"
        _exhaust(limited_app, ip)
        assert _get(limited_app, ip).status_code == 429
        assert _get(limited_app, "198.51.100.14").status_code == 200

    def test_a_forged_token_does_not_pick_a_user_bucket(self, limited_app):
        ip = "198.51.100.15"
        _exhaust(limited_app, ip)
        forged = _token(limited_app)[:-4] + "AAAA"
        assert _get(limited_app, ip, forged).status_code == 429


class TestPreflights:
    def test_options_requests_are_not_counted(self, limited_app):
        ip = "198.51.100.20"
        for _ in range(70):
            assert _get(limited_app, ip, method="OPTIONS").status_code == 200
        assert _get(limited_app, ip).status_code == 200

    def test_the_browser_may_cache_the_preflight_and_read_retry_after(self, limited_app):
        preflight = _get(limited_app, "198.51.100.21", method="OPTIONS")
        assert preflight.headers.get("Access-Control-Max-Age") == "86400"
        ip = "198.51.100.22"
        _exhaust(limited_app, ip)
        blocked = limited_app.test_client().get(
            APP_VERSION,
            headers={"X-Forwarded-For": ip, "Origin": "http://localhost:3000"},
            environ_base={"REMOTE_ADDR": "10.1.0.9"},
        )
        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers.get("Access-Control-Expose-Headers", "").lower()


class TestEvery429SaysHowLongToWait:
    def test_generic_429_has_retry_after_and_a_readable_message(self, limited_app):
        ip = "198.51.100.30"
        _exhaust(limited_app, ip)
        blocked = _get(limited_app, ip)
        assert blocked.status_code == 429
        body = blocked.get_json()
        assert body["error"] == "TooManyRequests"
        assert body["status_code"] == 429
        wait = int(blocked.headers["Retry-After"])
        assert 0 < wait <= 60
        assert body["retry_after"] == wait
        assert "per 1 minute" not in body["message"]
        assert body["message"].startswith("Too many requests. Try again in ")
