"""Sign in with a phone number + SMS code: /auth/otp/request, /auth/otp/verify, admin phone field."""

from __future__ import annotations

import re

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


MEMBER_PHONE = "+33900000123"


def _code_from_sms(app) -> str:
    to, text = app._sms.sent[-1]
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match.group(1)


@pytest.fixture(autouse=True)
def _fresh_otp_table(invitation_app):
    """The app fixture is shared by the module; drop earlier codes so throttles start clean."""
    from app import db
    from app.infrastructure.database.models import LoginOtpOrm

    with invitation_app.app_context():
        db.session.query(LoginOtpOrm).delete()
        db.session.commit()
    yield


@pytest.fixture
def member_with_phone(inv_client, superadmin_token, invitation_app):
    """Give the member user a phone through the admin endpoint; returns the user id."""
    me = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    search = inv_client.get(
        "/api/v1/admin/users",
        query_string={"search": invitation_app._test_member_email},
        headers=_auth(superadmin_token),
    ).get_json()
    member_id = next(u["id"] for u in search["items"] if u["email"] == invitation_app._test_member_email)
    resp = inv_client.patch(
        f"/api/v1/admin/users/{member_id}", json={"phone": "09 00 00 01 23"}, headers=_auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["phone"] == MEMBER_PHONE
    assert me["id"] != member_id
    return member_id


class TestAdminPhone:
    def test_phone_is_normalised_searchable_and_unique(
        self, inv_client, superadmin_token, invitation_app, member_with_phone
    ):
        items = inv_client.get(
            "/api/v1/admin/users",
            query_string={"search": invitation_app._test_member_email},
            headers=_auth(superadmin_token),
        ).get_json()["items"]
        assert next(u for u in items if u["id"] == member_with_phone)["phone"] == MEMBER_PHONE

        # Another user cannot take the same number.
        outsider = next(
            u
            for u in inv_client.get(
                "/api/v1/admin/users",
                query_string={"search": invitation_app._test_outsider_email},
                headers=_auth(superadmin_token),
            ).get_json()["items"]
            if u["email"] == invitation_app._test_outsider_email
        )
        dup = inv_client.patch(
            f"/api/v1/admin/users/{outsider['id']}", json={"phone": MEMBER_PHONE}, headers=_auth(superadmin_token)
        )
        assert dup.status_code == 409

        bad = inv_client.patch(
            f"/api/v1/admin/users/{outsider['id']}", json={"phone": "hello"}, headers=_auth(superadmin_token)
        )
        assert bad.status_code == 400

        cleared = inv_client.patch(
            f"/api/v1/admin/users/{member_with_phone}", json={"phone": None}, headers=_auth(superadmin_token)
        )
        assert cleared.status_code == 200 and cleared.get_json()["phone"] is None

    def test_company_admin_cannot_set_phone(self, inv_client, admin_token, member_with_phone):
        resp = inv_client.patch(
            f"/api/v1/admin/users/{member_with_phone}", json={"phone": "0900000999"}, headers=_auth(admin_token)
        )
        assert resp.status_code == 403


class TestOtpLogin:
    def test_request_then_verify_signs_in(self, inv_client, invitation_app, member_with_phone):
        sent_before = len(invitation_app._sms.sent)
        resp = inv_client.post("/api/v1/auth/otp/request", json={"phone": "0900000123"})
        assert resp.status_code == 202, resp.get_json()
        assert resp.get_json()["expires_in"] == 300
        assert len(invitation_app._sms.sent) == sent_before + 1
        assert invitation_app._sms.sent[-1][0] == MEMBER_PHONE
        code = _code_from_sms(invitation_app)

        ok = inv_client.post("/api/v1/auth/otp/verify", json={"phone": "+33 9 00 00 01 23", "code": code})
        assert ok.status_code == 200, ok.get_json()
        body = ok.get_json()
        assert body["user"]["email"] == invitation_app._test_member_email
        assert body["user"]["phone"] == MEMBER_PHONE
        me = inv_client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
        assert me.status_code == 200 and me.get_json()["id"] == member_with_phone
        # Same companies[] shape as /auth/me (shared _login_response helper).
        assert body["user"]["companies"] == me.get_json()["companies"]

        # A code is single-use.
        again = inv_client.post("/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": code})
        assert again.status_code == 401

    def test_unknown_phone_gets_202_without_sms(self, inv_client, invitation_app):
        sent_before = len(invitation_app._sms.sent)
        resp = inv_client.post("/api/v1/auth/otp/request", json={"phone": "0999999999"})
        assert resp.status_code == 202
        assert len(invitation_app._sms.sent) == sent_before
        verify = inv_client.post("/api/v1/auth/otp/verify", json={"phone": "0999999999", "code": "123456"})
        assert verify.status_code == 401

    def test_invalid_inputs(self, inv_client):
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": "abc"}).status_code == 400
        assert inv_client.post("/api/v1/auth/otp/verify", json={"phone": "0900000123", "code": "12"}).status_code == 400

    def test_sign_in_refuses_numbers_outside_france(self, inv_client, invitation_app):
        """Codes leave through a French gateway, so a foreign number is turned away with no SMS."""
        sent_before = len(invitation_app._sms.sent)
        for foreign in ("+84912345678", "0084912345678", "+442079460958"):
            resp = inv_client.post("/api/v1/auth/otp/request", json={"phone": foreign})
            assert resp.status_code == 400, (foreign, resp.get_json())
            verify = inv_client.post("/api/v1/auth/otp/verify", json={"phone": foreign, "code": "123456"})
            assert verify.status_code == 400, (foreign, verify.get_json())
        assert len(invitation_app._sms.sent) == sent_before

    def test_resend_is_throttled(self, inv_client, invitation_app, member_with_phone):
        first = inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE})
        assert first.status_code == 202
        second = inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE})
        assert second.status_code == 429

    def test_wrong_code_locks_after_five_attempts(self, inv_client, invitation_app, member_with_phone):
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE}).status_code == 202
        code = _code_from_sms(invitation_app)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            assert (
                inv_client.post("/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": wrong}).status_code
                == 401
            )
        # Even the right code is refused once the attempts are used up.
        assert inv_client.post("/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": code}).status_code == 401

    def test_persistent_policy_applies_to_otp_login_and_logout_revokes(
        self, inv_client, invitation_app, member_with_phone
    ):
        from flask_jwt_extended import decode_token

        invitation_app.config["REFRESH_TOKEN_POLICY"] = "persistent"
        try:
            assert inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE}).status_code == 202
            body = inv_client.post(
                "/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": _code_from_sms(invitation_app)}
            ).get_json()
        finally:
            invitation_app.config["REFRESH_TOKEN_POLICY"] = "expiring"
        with invitation_app.app_context():
            claims = decode_token(body["refresh_token"], allow_expired=True)
        assert "exp" not in claims and claims["persistent"] is True
        assert inv_client.post("/api/v1/auth/refresh", headers=_auth(body["refresh_token"])).status_code == 200
        out = inv_client.post(
            "/api/v1/auth/logout", headers=_auth(body["access_token"]), json={"refresh_token": body["refresh_token"]}
        )
        assert out.status_code == 200
        assert inv_client.post("/api/v1/auth/refresh", headers=_auth(body["refresh_token"])).status_code == 401


def _cookie_max_ages(response) -> dict:
    """Max-Age of each cookie the response sets, by name (None for a session cookie)."""
    ages = {}
    for header in response.headers.getlist("Set-Cookie"):
        match = re.search(r"Max-Age=(\d+)", header)
        ages[header.split("=", 1)[0]] = int(match.group(1)) if match else None
    return ages


class TestSessionCookies:
    """Browser cookies used to be session cookies: closing the browser signed the user out although the
    refresh token had days left. Each cookie now lives as long as the token it carries."""

    WEEK = 7 * 24 * 3600
    HALF_HOUR = 30 * 60

    def _sign_in(self, inv_client, invitation_app):
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE}).status_code == 202
        ok = inv_client.post(
            "/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": _code_from_sms(invitation_app)}
        )
        assert ok.status_code == 200, ok.get_json()
        return ok

    def test_sign_in_and_refresh_cookies_last_as_long_as_their_tokens(
        self, inv_client, invitation_app, member_with_phone
    ):
        ok = self._sign_in(inv_client, invitation_app)
        ages = _cookie_max_ages(ok)
        assert None not in ages.values(), ages
        assert self.WEEK - 60 <= ages["refresh_token_cookie"] <= self.WEEK
        assert self.HALF_HOUR - 60 <= ages["access_token_cookie"] <= self.HALF_HOUR

        refreshed = inv_client.post("/api/v1/auth/refresh", headers=_auth(ok.get_json()["refresh_token"]))
        assert refreshed.status_code == 200
        assert self.HALF_HOUR - 60 <= _cookie_max_ages(refreshed)["access_token_cookie"] <= self.HALF_HOUR

    def test_a_never_expiring_refresh_token_gets_a_year_long_cookie(
        self, inv_client, invitation_app, member_with_phone
    ):
        invitation_app.config["REFRESH_TOKEN_POLICY"] = "persistent"
        try:
            ok = self._sign_in(inv_client, invitation_app)
        finally:
            invitation_app.config["REFRESH_TOKEN_POLICY"] = "expiring"
        assert _cookie_max_ages(ok)["refresh_token_cookie"] == 31_540_000


class TestOtpTestCodeBypassProduction:
    """Endpoint-level companion to tests/unit/application/test_otp_test_code_bypass.py:
    the non-production OTP_TEST_CODE bypass must be refused through the real HTTP
    verify path under FLASK_ENV=production, not just in the isolated unit test —
    this is the one piece of phone-only auth that makes the system weaker, so its
    production-refusal is proven end to end, not just at the function level.
    """

    def test_production_refuses_the_bypass_code_even_when_configured(
        self, inv_client, invitation_app, member_with_phone, monkeypatch
    ):
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": MEMBER_PHONE}).status_code == 202
        real_code = _code_from_sms(invitation_app)

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("OTP_TEST_CODE", "424242")
        try:
            bypass_attempt = inv_client.post("/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": "424242"})
            assert bypass_attempt.status_code == 401, bypass_attempt.get_json()
        finally:
            monkeypatch.undo()

        # The real code from the request above still works normally once
        # FLASK_ENV/OTP_TEST_CODE are back to this test session's defaults.
        ok = inv_client.post("/api/v1/auth/otp/verify", json={"phone": MEMBER_PHONE, "code": real_code})
        assert ok.status_code == 200, ok.get_json()
