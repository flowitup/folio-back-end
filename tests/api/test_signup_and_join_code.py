"""Mobile onboarding: phone sign-up (SMS code + display name) and joining a company by its join code."""

from __future__ import annotations

import re
import uuid

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _code_from_sms(app) -> str:
    _to, text = app._sms.sent[-1]
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match.group(1)


def _signup(client, app, phone: str, name: str) -> dict:
    assert client.post("/api/v1/auth/signup/request", json={"phone": phone}).status_code == 202
    resp = client.post(
        "/api/v1/auth/signup/verify", json={"phone": phone, "code": _code_from_sms(app), "display_name": name}
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _make_company(client, token: str) -> dict:
    resp = client.post(
        "/api/v1/companies",
        json={"legal_name": f"Chantier {uuid.uuid4().hex[:6]}", "address": "1 rue de Paris"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


@pytest.fixture(autouse=True)
def _fresh_otp_table(invitation_app):
    from app import db
    from app.infrastructure.database.models import LoginOtpOrm

    with invitation_app.app_context():
        db.session.query(LoginOtpOrm).delete()
        db.session.commit()
    yield


class TestPhoneSignup:
    def test_signup_creates_account_and_signs_in(self, inv_client, invitation_app):
        phone = "06 00 00 11 22"
        body = _signup(inv_client, invitation_app, phone, "Nguyen Van A")
        assert body["user"]["phone"] == "+33600001122"
        me = inv_client.get("/api/v1/auth/me", headers=_auth(body["access_token"])).get_json()
        assert me["phone"] == "+33600001122"
        assert me["email"].endswith("@no-email.folio.flowitup.com")
        # A fresh account belongs to no company yet: the app shows the join screen.
        mine = inv_client.get("/api/v1/companies", headers=_auth(body["access_token"])).get_json()
        assert mine["items"] == []
        # The phone can now sign in with the regular OTP flow (the per-phone resend throttle is
        # shared with sign-up codes, so a code straight after sign-up answers 429 — cleared here).
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": phone}).status_code == 429
        from app import db
        from app.infrastructure.database.models import LoginOtpOrm

        with invitation_app.app_context():
            db.session.query(LoginOtpOrm).delete()
            db.session.commit()
        assert inv_client.post("/api/v1/auth/otp/request", json={"phone": phone}).status_code == 202
        login = inv_client.post(
            "/api/v1/auth/otp/verify", json={"phone": phone, "code": _code_from_sms(invitation_app)}
        )
        assert login.status_code == 200

    def test_signed_up_users_appear_in_admin_search(self, inv_client, invitation_app, superadmin_token):
        # The placeholder email must survive the strict EmailStr of the admin search response.
        _signup(inv_client, invitation_app, "0600002233", "Search Me")
        resp = inv_client.get(
            "/api/v1/admin/users", query_string={"search": "Search Me"}, headers=_auth(superadmin_token)
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
        assert any(u["display_name"] == "Search Me" and u["phone"] == "+33600002233" for u in resp.get_json()["items"])

    def test_signup_refuses_registered_phone(self, inv_client, invitation_app):
        _signup(inv_client, invitation_app, "0600003344", "Someone")
        resp = inv_client.post("/api/v1/auth/signup/request", json={"phone": "0600003344"})
        assert resp.status_code == 409

    def test_signup_wrong_code_and_missing_name(self, inv_client, invitation_app):
        assert inv_client.post("/api/v1/auth/signup/request", json={"phone": "0600005566"}).status_code == 202
        wrong = inv_client.post(
            "/api/v1/auth/signup/verify", json={"phone": "0600005566", "code": "000000", "display_name": "X"}
        )
        assert wrong.status_code == 401
        no_name = inv_client.post(
            "/api/v1/auth/signup/verify",
            json={"phone": "0600005566", "code": _code_from_sms(invitation_app), "display_name": ""},
        )
        assert no_name.status_code == 400

    def test_signup_disabled_in_email_mode_and_config_flag(self, inv_client, invitation_app):
        invitation_app.config["LOGIN_MODE"] = "email"
        try:
            assert inv_client.get("/api/v1/auth/config").get_json()["signup"] is False
            assert inv_client.post("/api/v1/auth/signup/request", json={"phone": "0600007788"}).status_code == 404
        finally:
            invitation_app.config["LOGIN_MODE"] = "both"
        assert inv_client.get("/api/v1/auth/config").get_json()["signup"] is True


class TestJoinCode:
    def test_superadmin_issues_code_member_joins_once(self, inv_client, invitation_app, superadmin_token):
        company = _make_company(inv_client, superadmin_token)
        issued = inv_client.post(f"/api/v1/companies/{company['id']}/join-code", headers=_auth(superadmin_token))
        assert issued.status_code == 200
        code = issued.get_json()["join_code"]
        assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", code)
        # Superadmin sees the code on the company; members never do.
        assert (
            inv_client.get(f"/api/v1/companies/{company['id']}", headers=_auth(superadmin_token)).get_json()[
                "join_code"
            ]
            == code
        )

        newcomer = _signup(inv_client, invitation_app, "0600009900", "Worker One")
        token = newcomer["access_token"]
        joined = inv_client.post(
            "/api/v1/companies/join", json={"code": f" {code[:4].lower()}-{code[4:]} "}, headers=_auth(token)
        )
        assert joined.status_code == 200, joined.get_json()
        assert joined.get_json()["id"] == company["id"]
        assert "join_code" not in joined.get_json()

        mine = inv_client.get("/api/v1/companies", headers=_auth(token)).get_json()["items"]
        assert [m["company"]["id"] for m in mine] == [company["id"]]
        assert mine[0]["access"]["role"] == "member" and mine[0]["access"]["is_primary"] is True

        again = inv_client.post("/api/v1/companies/join", json={"code": code}, headers=_auth(token))
        assert again.status_code == 409

    def test_unknown_and_revoked_codes(self, inv_client, invitation_app, superadmin_token):
        company = _make_company(inv_client, superadmin_token)
        code = inv_client.post(
            f"/api/v1/companies/{company['id']}/join-code", headers=_auth(superadmin_token)
        ).get_json()["join_code"]
        user = _signup(inv_client, invitation_app, "0600001010", "Worker Two")
        assert (
            inv_client.post(
                "/api/v1/companies/join", json={"code": "ZZZZ9999"}, headers=_auth(user["access_token"])
            ).status_code
            == 404
        )
        assert (
            inv_client.delete(
                f"/api/v1/companies/{company['id']}/join-code", headers=_auth(superadmin_token)
            ).status_code
            == 204
        )
        assert (
            inv_client.post(
                "/api/v1/companies/join", json={"code": code}, headers=_auth(user["access_token"])
            ).status_code
            == 404
        )

    def test_only_superadmin_manages_codes(self, inv_client, invitation_app, superadmin_token, member_token):
        company = _make_company(inv_client, superadmin_token)
        assert (
            inv_client.post(f"/api/v1/companies/{company['id']}/join-code", headers=_auth(member_token)).status_code
            == 403
        )

    def test_join_second_company(self, inv_client, invitation_app, superadmin_token):
        first = _make_company(inv_client, superadmin_token)
        second = _make_company(inv_client, superadmin_token)
        codes = [
            inv_client.post(f"/api/v1/companies/{c['id']}/join-code", headers=_auth(superadmin_token)).get_json()[
                "join_code"
            ]
            for c in (first, second)
        ]
        user = _signup(inv_client, invitation_app, "0600002020", "Worker Three")
        for code in codes:
            assert (
                inv_client.post(
                    "/api/v1/companies/join", json={"code": code}, headers=_auth(user["access_token"])
                ).status_code
                == 200
            )
        mine = inv_client.get("/api/v1/companies", headers=_auth(user["access_token"])).get_json()["items"]
        assert {m["company"]["id"] for m in mine} == {first["id"], second["id"]}
        assert sum(1 for m in mine if m["access"]["is_primary"]) == 1
