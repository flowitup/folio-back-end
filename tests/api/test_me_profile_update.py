"""PATCH /auth/me — a signed-in user edits their own display name and phone (Settings › Profile)."""

from __future__ import annotations

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def me(inv_client, superadmin_token):
    return inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()


def test_requires_auth(inv_client):
    assert inv_client.patch("/api/v1/auth/me", json={"phone": "06 00 00 00 99"}).status_code == 401


def _give_phone(client, token: str, user_id: str, phone: str) -> None:
    """Set the caller's sign-in phone the only way allowed: through platform ops."""
    resp = client.patch(f"/api/v1/admin/users/{user_id}", json={"phone": phone}, headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()


def test_updates_display_name_and_accepts_the_current_phone_in_any_format(inv_client, superadmin_token, me):
    _give_phone(inv_client, superadmin_token, me["id"], "06 00 00 00 99")
    resp = inv_client.patch(
        "/api/v1/auth/me",
        json={"display_name": "  Camille  ", "phone": "06 00 00 00 99"},
        headers=_auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["id"] == me["id"]
    assert body["display_name"] == "Camille"
    assert body["phone"] == "+33600000099"
    assert body["email"] == me["email"]  # e-mail is never editable here
    assert "permissions" in body and "companies" in body

    same = inv_client.patch("/api/v1/auth/me", json={"phone": "+33600000099"}, headers=_auth(superadmin_token))
    assert same.status_code == 200


@pytest.mark.parametrize("phone", [None, "", "   "])
def test_refuses_to_clear_the_sign_in_phone(inv_client, superadmin_token, me, phone):
    _give_phone(inv_client, superadmin_token, me["id"], "06 00 00 00 98")
    resp = inv_client.patch("/api/v1/auth/me", json={"phone": phone}, headers=_auth(superadmin_token))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "PhoneChangeNotAllowed"
    again = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    assert again["phone"] == "+33600000098"


@pytest.mark.parametrize("phone", ["06 00 00 00 96", "+447700900999"])
def test_refuses_an_unverified_new_phone(inv_client, superadmin_token, me, phone):
    _give_phone(inv_client, superadmin_token, me["id"], "06 00 00 00 98")
    resp = inv_client.patch(
        "/api/v1/auth/me", json={"display_name": "Kept?", "phone": phone}, headers=_auth(superadmin_token)
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "PhoneChangeNotAllowed"
    again = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    assert again["phone"] == "+33600000098"
    assert again["display_name"] != "Kept?"  # nothing is saved when the request is refused


def test_platform_ops_can_still_clear_a_phone(inv_client, superadmin_token, me):
    _give_phone(inv_client, superadmin_token, me["id"], "06 00 00 00 95")
    resp = inv_client.patch(f"/api/v1/admin/users/{me['id']}", json={"phone": None}, headers=_auth(superadmin_token))
    assert resp.status_code == 200
    again = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    assert again["phone"] is None


def test_rejects_invalid_phone_and_empty_body(inv_client, superadmin_token):
    assert (
        inv_client.patch("/api/v1/auth/me", json={"phone": "not-a-number"}, headers=_auth(superadmin_token)).status_code
        == 400
    )
    assert inv_client.patch("/api/v1/auth/me", json={}, headers=_auth(superadmin_token)).status_code == 400


def test_phone_taken_by_another_user_is_refused_too(inv_client, superadmin_token, invitation_app):
    search = inv_client.get(
        "/api/v1/admin/users",
        query_string={"search": invitation_app._test_member_email},
        headers=_auth(superadmin_token),
    ).get_json()
    member_id = next(u["id"] for u in search["items"] if u["email"] == invitation_app._test_member_email)
    _give_phone(inv_client, superadmin_token, member_id, "06 00 00 00 97")

    resp = inv_client.patch("/api/v1/auth/me", json={"phone": "+33600000097"}, headers=_auth(superadmin_token))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "PhoneChangeNotAllowed"
