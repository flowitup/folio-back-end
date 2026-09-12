"""Integration tests for the API-key authentication seam.

Covers app/api/_helpers/api_key_request_auth.py end-to-end: a valid key
authenticates an ordinary endpoint exactly as its owner, an unknown/tampered
key is a plain 401 (no separate error shape), the api-keys blueprint itself
rejects a caller authenticated BY a key, last_used_at tracking, and that an
ordinary JWT request is unaffected when no API-key header is present.

CRUD mechanics for /api/v1/api-keys itself live in
test_api_keys_endpoints.py.
"""

from __future__ import annotations

import uuid


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _api_key_header(token: str) -> dict:
    return {"X-API-Key": token}


def _create_key(client, token: str, name: str = "Auth test key") -> dict:
    resp = client.post("/api/v1/api-keys", json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


_PROJECTS_URL = "/api/v1/projects"


# ===========================================================================
# A valid key authenticates an ordinary endpoint, exactly as its owner
# ===========================================================================


class TestApiKeyAuthenticatesOwner:
    def test_x_api_key_header_matches_owner_jwt(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)

        via_jwt = inv_client.get(_PROJECTS_URL, headers=_auth(member_token))
        via_key = inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"]))

        assert via_jwt.status_code == 200
        assert via_key.status_code == 200
        jwt_ids = {p["id"] for p in via_jwt.get_json()["projects"]}
        key_ids = {p["id"] for p in via_key.get_json()["projects"]}
        assert key_ids == jwt_ids
        assert via_key.get_json()["total"] == via_jwt.get_json()["total"]

    def test_authorization_bearer_header_also_works(self, inv_client, admin_token):
        """Task spec supports both header forms; Bearer takes precedence over X-API-Key."""
        created = _create_key(inv_client, admin_token)

        via_jwt = inv_client.get(_PROJECTS_URL, headers=_auth(admin_token))
        via_key = inv_client.get(_PROJECTS_URL, headers=_auth(created["token"]))

        assert via_key.status_code == 200
        assert {p["id"] for p in via_key.get_json()["projects"]} == {p["id"] for p in via_jwt.get_json()["projects"]}


# ===========================================================================
# Unknown / tampered key → plain 401, no separate error shape
# ===========================================================================


class TestApiKeyRejectsUnknownOrTampered:
    def test_401_unknown_key_never_issued(self, inv_client):
        bogus = "folio_sk_" + "a" * 43
        resp = inv_client.get(_PROJECTS_URL, headers=_api_key_header(bogus))
        assert resp.status_code == 401

    def test_401_tampered_key(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)
        real_token = created["token"]
        tampered = real_token[:-1] + ("x" if real_token[-1] != "x" else "y")

        resp = inv_client.get(_PROJECTS_URL, headers=_api_key_header(tampered))
        assert resp.status_code == 401

    def test_401_non_folio_sk_credential_is_untouched_and_rejected(self, inv_client):
        """A credential that never matches API_KEY_PREFIX takes the ordinary JWT path (and fails it)."""
        resp = inv_client.get(_PROJECTS_URL, headers=_auth("not-a-real-jwt"))
        assert resp.status_code == 401


# ===========================================================================
# Self-escalation guard: a key can never manage API keys
# ===========================================================================


class TestApiKeyCannotManageApiKeys:
    def test_403_list(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)
        resp = inv_client.get("/api/v1/api-keys", headers=_api_key_header(created["token"]))
        assert resp.status_code == 403

    def test_403_create(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)
        resp = inv_client.post(
            "/api/v1/api-keys", json={"name": "Minted by a key"}, headers=_api_key_header(created["token"])
        )
        assert resp.status_code == 403

    def test_403_revoke(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)
        resp = inv_client.delete(f"/api/v1/api-keys/{uuid.uuid4()}", headers=_api_key_header(created["token"]))
        assert resp.status_code == 403

    def test_403_even_to_revoke_itself(self, inv_client, member_token):
        created = _create_key(inv_client, member_token)
        resp = inv_client.delete(f"/api/v1/api-keys/{created['id']}", headers=_api_key_header(created["token"]))
        assert resp.status_code == 403


# ===========================================================================
# last_used_at tracking
# ===========================================================================


class TestLastUsedAtTracking:
    def test_null_at_creation_then_populated_after_first_use(self, inv_client, admin_token):
        created = _create_key(inv_client, admin_token)
        assert created["last_used_at"] is None

        used = inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"]))
        assert used.status_code == 200

        listing = inv_client.get("/api/v1/api-keys", headers=_auth(admin_token)).get_json()["api_keys"]
        matching = next(k for k in listing if k["id"] == created["id"])
        assert matching["last_used_at"] is not None


# ===========================================================================
# An ordinary JWT request is unaffected when no API-key header is present
# ===========================================================================


class TestNormalJwtRequestUnaffected:
    def test_plain_jwt_request_behaves_normally(self, inv_client, member_token):
        resp = inv_client.get(_PROJECTS_URL, headers=_auth(member_token))
        assert resp.status_code == 200

    def test_plain_jwt_can_still_manage_own_api_keys(self, inv_client, member_token):
        """Sanity check that the api-keys self-escalation guard only fires for actual API-key auth."""
        resp = inv_client.get("/api/v1/api-keys", headers=_auth(member_token))
        assert resp.status_code == 200


# ===========================================================================
# What a key may NOT do — the denial direction
#
# A key inherits its owner's permissions in full, which governs what it may do
# with those permissions. It must NOT be able to change the account's
# authentication factor or administer accounts: users.phone IS the sign-in
# identity, so a caller able to rewrite it turns a leaked, never-expiring key
# into permanent account takeover — rewrite the phone, receive the SMS, and the
# resulting session is an ordinary interactive one that no longer looks
# key-authenticated, so it walks past the key-management guard and locks the
# real owner out.
# ===========================================================================


class TestApiKeyCannotMutateIdentity:
    def test_patch_auth_me_with_key_is_forbidden(self, inv_client, member_token, invitation_app):
        """The takeover vector: a key must not rewrite its owner's sign-in phone."""
        created = _create_key(inv_client, member_token, name="identity probe")

        resp = inv_client.patch(
            "/api/v1/auth/me",
            json={"phone": "+33600000000"},
            headers=_api_key_header(created["token"]),
        )

        assert resp.status_code == 403, resp.get_data(as_text=True)

        # And the phone is genuinely untouched, not merely reported as refused.
        from app import db
        from app.infrastructure.database.models import UserModel

        with invitation_app.app_context():
            user = db.session.get(UserModel, uuid.UUID(invitation_app._test_member_user_id))
            assert user.phone != "+33600000000"

    def test_patch_auth_me_with_key_is_forbidden_via_bearer_too(self, inv_client, member_token):
        """The guard keys on how the request authenticated, not on which header carried it."""
        created = _create_key(inv_client, member_token, name="identity probe bearer")

        resp = inv_client.patch(
            "/api/v1/auth/me",
            json={"display_name": "renamed by a key"},
            headers=_auth(created["token"]),
        )

        assert resp.status_code == 403

    def test_get_auth_me_with_key_still_works(self, inv_client, member_token, invitation_app):
        """Reads stay open — an automation needs to discover which account its key belongs to."""
        created = _create_key(inv_client, member_token, name="identity read")

        resp = inv_client.get("/api/v1/auth/me", headers=_api_key_header(created["token"]))

        assert resp.status_code == 200
        assert resp.get_json()["id"] == invitation_app._test_member_user_id

    def test_admin_mutations_with_key_are_forbidden(self, inv_client, admin_token, invitation_app):
        """Same rule on /admin/*: an ops-held key must not rewrite anyone else's phone."""
        created = _create_key(inv_client, admin_token, name="admin probe")

        resp = inv_client.patch(
            f"/api/v1/admin/users/{invitation_app._test_member_user_id}",
            json={"phone": "+33600000001"},
            headers=_api_key_header(created["token"]),
        )

        assert resp.status_code == 403

    def test_ordinary_session_may_still_patch_auth_me(self, inv_client, member_token):
        """The guard must not touch interactive callers — only key-authenticated ones."""
        resp = inv_client.patch(
            "/api/v1/auth/me",
            json={"display_name": "renamed by a person"},
            headers=_auth(member_token),
        )

        assert resp.status_code == 200


class TestCredentialExtraction:
    def test_lowercase_bearer_scheme_is_accepted(self, inv_client, member_token):
        """RFC 7235 makes the auth-scheme token case-insensitive; clients do send "bearer"."""
        created = _create_key(inv_client, member_token, name="lowercase scheme")

        resp = inv_client.get(
            _PROJECTS_URL,
            headers={"Authorization": f"bearer {created['token']}"},
        )

        assert resp.status_code == 200
