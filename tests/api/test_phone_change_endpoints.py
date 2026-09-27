"""Verified change of the sign-in phone: POST /auth/me/phone/request-code + /auth/me/phone/confirm."""

from __future__ import annotations

import re

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


CURRENT = "+33900000123"
NEW = "+33900000456"
REQUEST = "/api/v1/auth/me/phone/request-code"
CONFIRM = "/api/v1/auth/me/phone/confirm"


def _code_from_sms(app) -> str:
    match = re.search(r"\b(\d{6})\b", app._sms.sent[-1][1])
    assert match, app._sms.sent[-1]
    return match.group(1)


def _user_id(client, token: str, email: str) -> str:
    items = client.get("/api/v1/admin/users", query_string={"search": email}, headers=_auth(token)).get_json()["items"]
    return next(u["id"] for u in items if u["email"] == email)


def _set_phone(client, ops_token: str, user_id: str, phone) -> None:
    resp = client.patch(f"/api/v1/admin/users/{user_id}", json={"phone": phone}, headers=_auth(ops_token))
    assert resp.status_code == 200, resp.get_json()


@pytest.fixture(autouse=True)
def _fresh_codes(invitation_app):
    """The app fixture is shared by the module; drop earlier codes so throttles start clean."""
    from app import db
    from app.infrastructure.database.models import LoginOtpOrm

    with invitation_app.app_context():
        db.session.query(LoginOtpOrm).delete()
        db.session.commit()
    yield


@pytest.fixture
def member_id(inv_client, superadmin_token, invitation_app):
    """The member signs in with CURRENT; nobody holds NEW (phones persist across the module's tests)."""
    uid = _user_id(inv_client, superadmin_token, invitation_app._test_member_email)
    outsider = _user_id(inv_client, superadmin_token, invitation_app._test_outsider_email)
    _set_phone(inv_client, superadmin_token, outsider, None)
    _set_phone(inv_client, superadmin_token, uid, CURRENT)
    # An earlier test's confirmed change signed the member out of older sessions; start clean.
    from uuid import UUID

    from app import db
    from app.infrastructure.database.models import UserModel

    with invitation_app.app_context():
        db.session.query(UserModel).filter(UserModel.id == UUID(uid)).update({UserModel.tokens_valid_after: None})
        db.session.commit()
    return uid


def test_requires_auth(inv_client):
    assert inv_client.post(REQUEST, json={"phone": NEW}).status_code == 401
    assert inv_client.post(CONFIRM, json={"phone": NEW, "code": "123456"}).status_code == 401


def test_code_goes_to_the_new_number_and_confirm_switches_the_phone(
    inv_client, invitation_app, member_token, member_id
):
    sent_before = len(invitation_app._sms.sent)
    resp = inv_client.post(REQUEST, json={"phone": "09 00 00 04 56"}, headers=_auth(member_token))
    assert resp.status_code == 202, resp.get_json()
    assert resp.get_json()["expires_in"] == 300
    assert len(invitation_app._sms.sent) == sent_before + 1
    to, text = invitation_app._sms.sent[-1]
    assert to == NEW
    # Same language and shape as the sign-in SMS (ASCII Vietnamese, minutes).
    assert text.startswith("Folio: ma xac nhan so dien thoai moi") and "5 phut" in text

    ok = inv_client.post(
        CONFIRM, json={"phone": NEW, "code": _code_from_sms(invitation_app)}, headers=_auth(member_token)
    )
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["id"] == member_id and ok.get_json()["phone"] == NEW

    # The session carries on with the fresh tokens of the response; the ones it used are dead.
    fresh = ok.get_json()
    me = inv_client.get("/api/v1/auth/me", headers=_auth(fresh["access_token"]))
    assert me.status_code == 200 and me.get_json()["phone"] == NEW
    assert inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).status_code == 401
    refreshed = inv_client.post("/api/v1/auth/refresh", headers=_auth(fresh["refresh_token"]))
    assert refreshed.status_code == 200, refreshed.get_json()

    # The new number signs in; the old one no longer reaches the account (no SMS is sent to it).
    sent_before = len(invitation_app._sms.sent)
    assert inv_client.post("/api/v1/auth/otp/request", json={"phone": CURRENT}).status_code == 202
    assert len(invitation_app._sms.sent) == sent_before
    from app import db
    from app.infrastructure.database.models import LoginOtpOrm

    with invitation_app.app_context():
        db.session.query(LoginOtpOrm).delete()  # skip the resend throttle on the new number
        db.session.commit()
    assert inv_client.post("/api/v1/auth/otp/request", json={"phone": NEW}).status_code == 202
    signed_in = inv_client.post("/api/v1/auth/otp/verify", json={"phone": NEW, "code": _code_from_sms(invitation_app)})
    assert signed_in.status_code == 200 and signed_in.get_json()["user"]["id"] == member_id


def test_a_phone_change_code_never_signs_in(inv_client, invitation_app, member_token, member_id):
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    code = _code_from_sms(invitation_app)
    assert inv_client.post("/api/v1/auth/otp/verify", json={"phone": NEW, "code": code}).status_code == 401
    assert (
        inv_client.post(
            "/api/v1/auth/signup/verify", json={"phone": NEW, "code": code, "display_name": "Intruder"}
        ).status_code
        == 401
    )
    # Still usable for its own purpose.
    ok = inv_client.post(CONFIRM, json={"phone": NEW, "code": code}, headers=_auth(member_token))
    assert ok.status_code == 200, ok.get_json()


def test_a_sign_in_code_never_changes_a_number(
    inv_client, invitation_app, superadmin_token, member_token, member_id, outsider_token
):
    # The outsider signs in with NEW; that sign-in code must not let the member move onto NEW...
    outsider_id = _user_id(inv_client, superadmin_token, invitation_app._test_outsider_email)
    _set_phone(inv_client, superadmin_token, outsider_id, NEW)
    assert inv_client.post("/api/v1/auth/otp/request", json={"phone": NEW}).status_code == 202
    code = _code_from_sms(invitation_app)
    _set_phone(inv_client, superadmin_token, outsider_id, None)
    resp = inv_client.post(CONFIRM, json={"phone": NEW, "code": code}, headers=_auth(member_token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "InvalidCode"
    assert inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).get_json()["phone"] == CURRENT


def test_number_of_another_account_is_refused_without_saying_whose(
    inv_client, invitation_app, superadmin_token, member_token, member_id
):
    outsider_id = _user_id(inv_client, superadmin_token, invitation_app._test_outsider_email)
    _set_phone(inv_client, superadmin_token, outsider_id, NEW)
    sent_before = len(invitation_app._sms.sent)
    resp = inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token))
    assert resp.status_code == 409
    body = resp.get_json()
    assert invitation_app._test_outsider_email not in body["message"]
    assert len(invitation_app._sms.sent) == sent_before
    confirm = inv_client.post(CONFIRM, json={"phone": NEW, "code": "123456"}, headers=_auth(member_token))
    assert confirm.status_code == 409


def test_number_taken_between_request_and_confirm_is_refused(
    inv_client, invitation_app, superadmin_token, member_token, member_id
):
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    code = _code_from_sms(invitation_app)
    outsider_id = _user_id(inv_client, superadmin_token, invitation_app._test_outsider_email)
    _set_phone(inv_client, superadmin_token, outsider_id, NEW)
    resp = inv_client.post(CONFIRM, json={"phone": NEW, "code": code}, headers=_auth(member_token))
    assert resp.status_code == 409
    assert inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).get_json()["phone"] == CURRENT


@pytest.mark.parametrize("phone", ["abc", "+84912345678", "+442079460958"])
def test_invalid_or_foreign_numbers_are_refused(inv_client, invitation_app, member_token, member_id, phone):
    sent_before = len(invitation_app._sms.sent)
    assert inv_client.post(REQUEST, json={"phone": phone}, headers=_auth(member_token)).status_code == 400
    assert (
        inv_client.post(CONFIRM, json={"phone": phone, "code": "123456"}, headers=_auth(member_token)).status_code
        == 400
    )
    assert len(invitation_app._sms.sent) == sent_before


def test_current_number_is_refused(inv_client, member_token, member_id):
    resp = inv_client.post(REQUEST, json={"phone": "09 00 00 01 23"}, headers=_auth(member_token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "PhoneUnchanged"


def test_bad_code_shape_is_a_validation_error(inv_client, member_token, member_id):
    assert inv_client.post(CONFIRM, json={"phone": NEW, "code": "12"}, headers=_auth(member_token)).status_code == 400


def test_resend_is_throttled(inv_client, member_token, member_id):
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 429


def test_wrong_code_locks_after_five_attempts(inv_client, invitation_app, member_token, member_id):
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    code = _code_from_sms(invitation_app)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        resp = inv_client.post(CONFIRM, json={"phone": NEW, "code": wrong}, headers=_auth(member_token))
        # Not 401: the caller is signed in, and a 401 would make clients refresh and replay.
        assert resp.status_code == 400 and resp.get_json()["error"] == "InvalidCode"
    assert inv_client.post(CONFIRM, json={"phone": NEW, "code": code}, headers=_auth(member_token)).status_code == 400
    assert inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).get_json()["phone"] == CURRENT


def test_another_users_code_does_not_work(
    inv_client, invitation_app, superadmin_token, member_token, member_id, outsider_token
):
    """Two accounts asking for the same free number: only the code texted for your own request counts."""
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    member_code = _code_from_sms(invitation_app)
    resp = inv_client.post(CONFIRM, json={"phone": NEW, "code": member_code}, headers=_auth(outsider_token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "InvalidCode"
    assert inv_client.get("/api/v1/auth/me", headers=_auth(outsider_token)).get_json()["phone"] != NEW


def _token_issued_before_now(invitation_app, user_id: str, *, refresh: bool = False) -> str:
    """A token another device got at sign-in, a minute ago."""
    import time
    from uuid import UUID

    from flask_jwt_extended import create_access_token, create_refresh_token

    with invitation_app.app_context():
        claims = {"iat": int(time.time()) - 60}
        if refresh:
            return create_refresh_token(identity=str(UUID(user_id)), expires_delta=False, additional_claims=claims)
        return create_access_token(identity=str(UUID(user_id)), additional_claims=claims)


def test_confirmed_change_signs_every_other_device_out(inv_client, invitation_app, member_token, member_id):
    other_access = _token_issued_before_now(invitation_app, member_id)
    other_refresh = _token_issued_before_now(invitation_app, member_id, refresh=True)
    assert inv_client.get("/api/v1/auth/me", headers=_auth(other_access)).status_code == 200

    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    ok = inv_client.post(
        CONFIRM, json={"phone": NEW, "code": _code_from_sms(invitation_app)}, headers=_auth(member_token)
    )
    assert ok.status_code == 200, ok.get_json()

    # Other devices: access refused and no new access token from their (never-expiring) refresh token.
    assert inv_client.get("/api/v1/auth/me", headers=_auth(other_access)).status_code == 401
    assert inv_client.post("/api/v1/auth/refresh", headers=_auth(other_refresh)).status_code == 401
    # This device: signed in with the pair it was handed, also set as cookies for browsers.
    assert inv_client.get("/api/v1/auth/me", headers=_auth(ok.get_json()["access_token"])).status_code == 200
    cookies = " ".join(ok.headers.getlist("Set-Cookie"))
    assert "access_token_cookie=" in cookies or "access_token=" in cookies
    # A later sign-in is a new session and works as usual.
    from tests.auth_login_helper import mint_access_token

    later = mint_access_token(inv_client, invitation_app._test_member_email)
    assert inv_client.get("/api/v1/auth/me", headers=_auth(later)).status_code == 200


def test_refused_change_signs_nobody_out(inv_client, invitation_app, member_token, member_id):
    other_access = _token_issued_before_now(invitation_app, member_id)
    assert inv_client.post(REQUEST, json={"phone": NEW}, headers=_auth(member_token)).status_code == 202
    code = _code_from_sms(invitation_app)
    wrong = "000000" if code != "000000" else "111111"
    assert inv_client.post(CONFIRM, json={"phone": NEW, "code": wrong}, headers=_auth(member_token)).status_code == 400
    assert inv_client.get("/api/v1/auth/me", headers=_auth(other_access)).status_code == 200
    assert inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).status_code == 200
