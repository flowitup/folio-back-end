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


# --- QA3 auth-onboarding-19 / -20 -------------------------------------------------


def test_display_name_limit_is_80_and_it_cannot_be_emptied(inv_client, superadmin_token):
    ok = inv_client.patch("/api/v1/auth/me", json={"display_name": "é" * 80}, headers=_auth(superadmin_token))
    assert ok.status_code == 200
    too_long = inv_client.patch("/api/v1/auth/me", json={"display_name": "é" * 81}, headers=_auth(superadmin_token))
    assert too_long.status_code == 400
    for empty in ("   ", "", None):
        resp = inv_client.patch("/api/v1/auth/me", json={"display_name": empty}, headers=_auth(superadmin_token))
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "DisplayNameRequired"
    again = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
    assert again["display_name"] == "é" * 80


def _link_person(me: dict, name: str):
    from datetime import datetime, timezone
    from uuid import UUID, uuid4

    from app.domain.entities.person import Person
    from wiring import get_container

    repo = get_container().person_repo
    existing = repo.find_by_user_id(UUID(me["id"]))
    if existing is not None:  # the app fixture is shared by the module
        return repo.rename(existing.id, name), repo
    person = Person(
        id=uuid4(),
        name=name,
        normalized_name=Person.normalize(name),
        created_by_user_id=UUID(me["id"]),
        created_at=datetime.now(timezone.utc),
        user_id=UUID(me["id"]),
    )
    return repo.create(person), repo


def test_rename_updates_the_directory_entry_that_mirrored_the_old_name(inv_client, superadmin_token, me):
    inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 Đặng Văn Lâm"}, headers=_auth(superadmin_token))
    person, repo = _link_person(me, "QA3 Đặng Văn Lâm")

    resp = inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 Lâm Bùi"}, headers=_auth(superadmin_token))

    assert resp.status_code == 200
    assert repo.find_by_id(person.id).name == "QA3 Lâm Bùi"


def test_rename_keeps_a_name_an_admin_chose(inv_client, superadmin_token, me):
    inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 Sign-up name"}, headers=_auth(superadmin_token))
    person, repo = _link_person(me, "QA3 Chef d'équipe (admin label)")

    inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 New name"}, headers=_auth(superadmin_token))

    assert repo.find_by_id(person.id).name == "QA3 Chef d'équipe (admin label)"


def test_rename_replaces_a_directory_entry_named_after_the_phone(inv_client, superadmin_token, me):
    inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 Before"}, headers=_auth(superadmin_token))
    person, repo = _link_person(me, "+33 6 11 11 00 99")

    inv_client.patch("/api/v1/auth/me", json={"display_name": "QA3 After"}, headers=_auth(superadmin_token))

    assert repo.find_by_id(person.id).name == "QA3 After"
