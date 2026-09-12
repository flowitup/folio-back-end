"""Integration tests for the personal API-keys endpoints (3 routes).

A key inherits its owner's permissions in full, never expires, and any
signed-in user may create one and sees only their own (locked product
decisions) — so these tests exercise identity scoping and CRUD mechanics,
not a permission matrix. Authentication-seam behaviour (a key acting as its
owner, unknown/tampered keys, the api-keys self-escalation guard) lives in
test_api_key_authentication.py.
"""

from __future__ import annotations

import uuid


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _keys_url() -> str:
    return "/api/v1/api-keys"


def _key_url(key_id: str) -> str:
    return f"/api/v1/api-keys/{key_id}"


def _create_key(client, token: str, name: str = "Test key") -> dict:
    resp = client.post(_keys_url(), json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


# ===========================================================================
# POST /api/v1/api-keys  — create
# ===========================================================================


class TestCreateApiKeyEndpoint:
    def test_201_returns_token_exactly_once(self, inv_client, member_token):
        data = _create_key(inv_client, member_token, name="My automation key")
        assert data["name"] == "My automation key"
        assert "id" in data
        assert "created_at" in data
        assert data["last_used_at"] is None
        assert isinstance(data["token"], str) and data["token"].startswith("folio_sk_")
        assert data["prefix"] == data["token"][:16]

    def test_201_response_shape_complete(self, inv_client, member_token):
        data = _create_key(inv_client, member_token)
        required_keys = {"id", "name", "prefix", "created_at", "last_used_at", "token"}
        assert required_keys.issubset(data.keys())

    def test_401_unauthenticated(self, inv_client):
        resp = inv_client.post(_keys_url(), json={"name": "No auth"})
        assert resp.status_code == 401

    def test_422_blank_name(self, inv_client, member_token):
        resp = inv_client.post(_keys_url(), json={"name": ""}, headers=_auth(member_token))
        assert resp.status_code == 422

    def test_422_name_too_long(self, inv_client, member_token):
        resp = inv_client.post(_keys_url(), json={"name": "A" * 101}, headers=_auth(member_token))
        assert resp.status_code == 422

    def test_400_whitespace_only_name(self, inv_client, member_token):
        """A name of all whitespace passes Pydantic min_length but fails domain validation after strip → 400."""
        resp = inv_client.post(_keys_url(), json={"name": "   "}, headers=_auth(member_token))
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "BadRequest"

    def test_422_missing_name(self, inv_client, member_token):
        resp = inv_client.post(_keys_url(), json={}, headers=_auth(member_token))
        assert resp.status_code == 422

    def test_409_when_cap_of_20_reached(self, inv_client, superadmin_token):
        """This persona must not be used to create keys anywhere else in this module."""
        for i in range(20):
            resp = inv_client.post(_keys_url(), json={"name": f"Cap key {i}"}, headers=_auth(superadmin_token))
            assert resp.status_code == 201, f"key {i} failed: {resp.get_data(as_text=True)}"

        resp = inv_client.post(_keys_url(), json={"name": "One too many"}, headers=_auth(superadmin_token))
        assert resp.status_code == 409


# ===========================================================================
# GET /api/v1/api-keys  — list
# ===========================================================================


class TestListApiKeysEndpoint:
    def test_200_wraps_items_in_api_keys(self, inv_client, admin_token):
        _create_key(inv_client, admin_token, name="Admin key 1")
        resp = inv_client.get(_keys_url(), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "api_keys" in data
        assert isinstance(data["api_keys"], list)
        assert any(k["name"] == "Admin key 1" for k in data["api_keys"])

    def test_200_never_exposes_token_or_hash(self, inv_client, admin_token):
        _create_key(inv_client, admin_token, name="Admin key 2")
        resp = inv_client.get(_keys_url(), headers=_auth(admin_token))
        assert resp.status_code == 200
        for item in resp.get_json()["api_keys"]:
            assert "token" not in item
            assert "token_hash" not in item

    def test_200_only_shows_own_keys(self, inv_client, admin_token, member_token):
        admin_key = _create_key(inv_client, admin_token, name="Admin-only key")
        member_key = _create_key(inv_client, member_token, name="Member-only key")

        admin_ids = {k["id"] for k in inv_client.get(_keys_url(), headers=_auth(admin_token)).get_json()["api_keys"]}
        member_ids = {k["id"] for k in inv_client.get(_keys_url(), headers=_auth(member_token)).get_json()["api_keys"]}

        assert admin_key["id"] in admin_ids
        assert admin_key["id"] not in member_ids
        assert member_key["id"] in member_ids
        assert member_key["id"] not in admin_ids

    def test_401_unauthenticated(self, inv_client):
        resp = inv_client.get(_keys_url())
        assert resp.status_code == 401


# ===========================================================================
# DELETE /api/v1/api-keys/<key_id>  — revoke
# ===========================================================================


class TestRevokeApiKeyEndpoint:
    def test_204_revokes_own_key(self, inv_client, admin_token):
        created = _create_key(inv_client, admin_token, name="To revoke")
        resp = inv_client.delete(_key_url(created["id"]), headers=_auth(admin_token))
        assert resp.status_code == 204

        remaining = inv_client.get(_keys_url(), headers=_auth(admin_token)).get_json()["api_keys"]
        assert all(k["id"] != created["id"] for k in remaining)

    def test_404_unknown_key(self, inv_client, admin_token):
        resp = inv_client.delete(_key_url(str(uuid.uuid4())), headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_404_another_users_key_is_not_403(self, inv_client, admin_token, member_token):
        """It is not the caller's business whether that id belongs to someone else — 404, not 403."""
        admin_key = _create_key(inv_client, admin_token, name="Admin's private key")
        resp = inv_client.delete(_key_url(admin_key["id"]), headers=_auth(member_token))
        assert resp.status_code == 404

        # And the admin's key must still exist (member's failed attempt didn't revoke it).
        remaining = inv_client.get(_keys_url(), headers=_auth(admin_token)).get_json()["api_keys"]
        assert any(k["id"] == admin_key["id"] for k in remaining)

    def test_401_unauthenticated(self, inv_client, admin_token):
        created = _create_key(inv_client, admin_token, name="Needs auth to revoke")
        resp = inv_client.delete(_key_url(created["id"]))
        assert resp.status_code == 401

    def test_revoked_key_no_longer_authenticates(self, inv_client, admin_token):
        created = _create_key(inv_client, admin_token, name="Live then dead")
        token = created["token"]

        ok = inv_client.get("/api/v1/projects", headers={"X-API-Key": token})
        assert ok.status_code == 200

        revoke_resp = inv_client.delete(_key_url(created["id"]), headers=_auth(admin_token))
        assert revoke_resp.status_code == 204

        dead = inv_client.get("/api/v1/projects", headers={"X-API-Key": token})
        assert dead.status_code == 401
