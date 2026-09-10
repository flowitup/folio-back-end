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


def test_updates_display_name_and_normalises_phone(inv_client, superadmin_token, me):
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

    again = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    assert again["phone"] == "+33600000099"


def test_clears_phone_with_null(inv_client, superadmin_token):
    inv_client.patch("/api/v1/auth/me", json={"phone": "06 00 00 00 98"}, headers=_auth(superadmin_token))
    resp = inv_client.patch("/api/v1/auth/me", json={"phone": None}, headers=_auth(superadmin_token))
    assert resp.status_code == 200
    assert resp.get_json()["phone"] is None


def test_rejects_invalid_phone_and_empty_body(inv_client, superadmin_token):
    assert (
        inv_client.patch("/api/v1/auth/me", json={"phone": "not-a-number"}, headers=_auth(superadmin_token)).status_code
        == 400
    )
    assert inv_client.patch("/api/v1/auth/me", json={}, headers=_auth(superadmin_token)).status_code == 400


def test_phone_taken_by_another_user_is_a_conflict(inv_client, superadmin_token, invitation_app):
    search = inv_client.get(
        "/api/v1/admin/users",
        query_string={"search": invitation_app._test_member_email},
        headers=_auth(superadmin_token),
    ).get_json()
    member_id = next(u["id"] for u in search["items"] if u["email"] == invitation_app._test_member_email)
    given = inv_client.patch(
        f"/api/v1/admin/users/{member_id}", json={"phone": "06 00 00 00 97"}, headers=_auth(superadmin_token)
    )
    assert given.status_code == 200, given.get_json()

    resp = inv_client.patch("/api/v1/auth/me", json={"phone": "+33600000097"}, headers=_auth(superadmin_token))
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "Phone already in use"
