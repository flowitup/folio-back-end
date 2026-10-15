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


class TestApiKeyHonoursTheSessionCutoff:
    """A phone change ends every earlier session (users.tokens_valid_after); a key made before it is dead too."""

    def _set_cutoff_and_key_age(self, invitation_app, user_id: str, key_id: str, *, cutoff_ago_h: int, key_age_h: int):
        from datetime import datetime, timedelta, timezone

        from app import db
        from app.infrastructure.database.models import UserModel
        from app.infrastructure.database.models.api_key import ApiKeyOrm

        now = datetime.now(timezone.utc)
        with invitation_app.app_context():
            db.session.get(UserModel, uuid.UUID(user_id)).tokens_valid_after = now - timedelta(hours=cutoff_ago_h)
            db.session.get(ApiKeyOrm, uuid.UUID(key_id)).created_at = now - timedelta(hours=key_age_h)
            db.session.commit()

    def _clear_cutoff(self, invitation_app, user_id: str):
        from app import db
        from app.infrastructure.database.models import UserModel

        with invitation_app.app_context():
            db.session.get(UserModel, uuid.UUID(user_id)).tokens_valid_after = None
            db.session.commit()

    def test_key_created_before_the_cutoff_stops_authenticating(self, inv_client, member_token, invitation_app):
        created = _create_key(inv_client, member_token, name="predates the cut-off")
        user_id = invitation_app._test_member_user_id
        try:
            assert inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"])).status_code == 200
            self._set_cutoff_and_key_age(invitation_app, user_id, created["id"], cutoff_ago_h=1, key_age_h=2)
            assert inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"])).status_code == 401
        finally:
            self._clear_cutoff(invitation_app, user_id)

    def test_key_created_after_the_cutoff_keeps_working(self, inv_client, member_token, invitation_app):
        created = _create_key(inv_client, member_token, name="made after the cut-off")
        user_id = invitation_app._test_member_user_id
        try:
            self._set_cutoff_and_key_age(invitation_app, user_id, created["id"], cutoff_ago_h=2, key_age_h=1)
            assert inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"])).status_code == 200
        finally:
            self._clear_cutoff(invitation_app, user_id)


class TestApiKeyCannotPlantPersistence:
    """Push devices and notification settings outlive a revoked key, so a key may not write them."""

    def test_push_device_registration_and_removal_are_forbidden(self, inv_client, member_token):
        created = _create_key(inv_client, member_token, name="push probe")
        body = {"token": "ExponentPushToken[attacker-device-0000]", "platform": "ios"}
        assert (
            inv_client.post("/api/v1/push/devices", json=body, headers=_api_key_header(created["token"])).status_code
            == 403
        )
        assert (
            inv_client.delete("/api/v1/push/devices", json=body, headers=_api_key_header(created["token"])).status_code
            == 403
        )

    def test_notification_settings_and_feed_writes_are_forbidden_but_reads_and_dismiss_stay(
        self, inv_client, member_token
    ):
        created = _create_key(inv_client, member_token, name="notification probe")
        headers = _api_key_header(created["token"])
        assert (
            inv_client.put("/api/v1/notifications/preferences", json={"chat": False}, headers=headers).status_code
            == 403
        )
        assert inv_client.post("/api/v1/notifications/events/read", headers=headers).status_code == 403
        assert inv_client.get("/api/v1/notifications", headers=headers).status_code != 403
        # The MCP plugin dismisses reminders with a key: that write is not blocked.
        dismissed = inv_client.post(f"/api/v1/notifications/{uuid.uuid4()}/dismiss", headers=headers)
        assert dismissed.status_code != 403


class TestCredentialExtraction:
    def test_lowercase_bearer_scheme_is_accepted(self, inv_client, member_token):
        """RFC 7235 makes the auth-scheme token case-insensitive; clients do send "bearer"."""
        created = _create_key(inv_client, member_token, name="lowercase scheme")

        resp = inv_client.get(
            _PROJECTS_URL,
            headers={"Authorization": f"bearer {created['token']}"},
        )

        assert resp.status_code == 200


# ===========================================================================
# A key may not plant a durable interactive seat: membership, access and
# invitation writes need an interactive session (default-deny route policy)
# ===========================================================================


def _admin_key(inv_client, admin_token) -> dict:
    return _api_key_header(_create_key(inv_client, admin_token, name="membership probe")["token"])


class TestApiKeyCannotPlantAccess:
    def _blocked_requests(self, app):
        company = app._test_company_id
        project = app._test_project_id
        target = app._test_member_user_id
        return [
            ("post", f"/api/v1/companies/{company}/access/{target}", {}),
            ("delete", f"/api/v1/companies/{company}/access/{target}", None),
            ("patch", f"/api/v1/companies/{company}/access/{target}/role", {"role": "admin"}),
            ("post", f"/api/v1/companies/{company}/join-code", {}),
            ("delete", f"/api/v1/companies/{company}/join-code", None),
            ("put", f"/api/v1/companies/{company}/members/{target}/grants", {}),
            ("delete", f"/api/v1/companies/{company}/members/{target}/grants", None),
            ("post", f"/api/v1/companies/{company}/members", {"phone": "+33600000002", "name": "X"}),
            ("post", f"/api/v1/companies/{company}/members/import", {}),
            ("delete", f"/api/v1/companies/{company}", None),
            ("delete", f"/api/v1/companies/{company}/access", None),
            ("post", "/api/v1/companies/join", {"code": "ABCD2345"}),
            ("post", "/api/v1/invitations", {"email": "x@example.com", "project_id": project}),
            ("post", f"/api/v1/invitations/{uuid.uuid4()}/revoke", {}),
            ("post", f"/api/v1/projects/{project}/users", {"user_id": target}),
            ("delete", f"/api/v1/projects/{project}/users/{target}", None),
            ("put", f"/api/v1/projects/{project}/assignments/{target}", {}),
            ("delete", f"/api/v1/projects/{project}/assignments/{target}", None),
            ("post", "/api/v1/chat/channels/x/messages", {"body": "hi"}),
        ]

    def test_every_membership_route_is_refused_for_an_admin_key(self, inv_client, admin_token, invitation_app):
        headers = _admin_key(inv_client, admin_token)
        for method, url, body in self._blocked_requests(invitation_app):
            kwargs = {"headers": headers} if body is None else {"headers": headers, "json": body}
            resp = getattr(inv_client, method)(url, **kwargs)
            assert resp.status_code == 403, (method, url, resp.get_data(as_text=True))
            assert resp.get_json()["status_code"] == 403
            assert resp.get_json()["error"] == "Forbidden"

    def test_nothing_changed_behind_the_refusals(self, inv_client, admin_token, invitation_app):
        from app import db
        from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

        headers = _admin_key(inv_client, admin_token)
        company = invitation_app._test_company_id
        member = invitation_app._test_member_user_id
        inv_client.patch(f"/api/v1/companies/{company}/access/{member}/role", json={"role": "admin"}, headers=headers)
        inv_client.delete(f"/api/v1/companies/{company}/access/{member}", headers=headers)

        with invitation_app.app_context():
            row = (
                db.session.query(UserCompanyAccessModel)
                .filter_by(user_id=uuid.UUID(member), company_id=uuid.UUID(company))
                .one()
            )
            assert row.role == "member"

    def test_the_interactive_session_is_not_refused_by_the_policy(self, inv_client, admin_token, invitation_app):
        resp = inv_client.post(
            "/api/v1/invitations",
            json={"email": "newcomer@example.com", "project_id": invitation_app._test_project_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

    def test_plugin_routes_stay_open_to_an_admin_key(self, inv_client, admin_token, invitation_app):
        headers = _admin_key(inv_client, admin_token)
        project = invitation_app._test_project_id

        assert inv_client.get(_PROJECTS_URL, headers=headers).status_code == 200
        assert inv_client.get("/api/v1/auth/me", headers=headers).status_code == 200
        assert (
            inv_client.get(f"/api/v1/companies/{invitation_app._test_company_id}", headers=headers).status_code == 200
        )
        assert inv_client.get(f"{_PROJECTS_URL}/{project}/members", headers=headers).status_code != 403

        created = inv_client.post(_PROJECTS_URL, json={"address": "1 rue de la Cle"}, headers=headers)
        assert created.status_code == 201, created.get_data(as_text=True)
        new_id = created.get_json()["id"]
        assert inv_client.put(f"{_PROJECTS_URL}/{new_id}", json={"name": "Renamed"}, headers=headers).status_code == 200
        assert inv_client.delete(f"{_PROJECTS_URL}/{new_id}", headers=headers).status_code == 204
        assert inv_client.post("/api/v1/labor/roles", json={"name": "Mason"}, headers=headers).status_code != 403
        assert (
            inv_client.post(f"{_PROJECTS_URL}/{project}/tasks", json={"title": "t"}, headers=headers).status_code != 403
        )

    def test_a_key_does_not_read_the_join_code_back(self, inv_client, admin_token, invitation_app):
        company = invitation_app._test_company_id
        issued = inv_client.post(f"/api/v1/companies/{company}/join-code", headers=_auth(admin_token))
        assert issued.status_code == 200, issued.get_data(as_text=True)
        url = f"/api/v1/companies/{company}"

        assert "join_code" in inv_client.get(url, headers=_auth(admin_token)).get_json()
        assert "join_code" not in inv_client.get(url, headers=_admin_key(inv_client, admin_token)).get_json()


class TestApiKeyRoutePolicyMatrix:
    """A new write route is closed to keys until it is listed on purpose."""

    # Blueprints with write routes that are deliberately closed to keys (or guarded by their own hook).
    CLOSED_BLUEPRINTS = {
        "admin",
        "api_keys",
        "auth",
        "chat",
        "companies",
        "invitations",
        "projects",
        "push",
        "users_me",
    }

    def _write_rules(self, app):
        for rule in app.url_map.iter_rules():
            if rule.methods and rule.methods - {"GET", "HEAD", "OPTIONS"}:
                yield rule

    def test_every_blueprint_with_writes_is_decided(self, invitation_app):
        from app.api._helpers.api_key_request_auth import KEY_WRITE_BLUEPRINTS

        seen = {r.endpoint.rsplit(".", 1)[0] for r in self._write_rules(invitation_app) if "." in r.endpoint}
        undecided = seen - KEY_WRITE_BLUEPRINTS - self.CLOSED_BLUEPRINTS - {"__test__"}
        assert not undecided, f"decide whether a key may write to {sorted(undecided)} (api_key_request_auth.py)"

    def test_key_writable_endpoints_in_mixed_blueprints_are_pinned(self):
        from app.api._helpers.api_key_request_auth import KEY_WRITE_ENDPOINTS

        assert KEY_WRITE_ENDPOINTS == {
            "companies.create_company",
            "companies.update_company",
            "projects.create_project",
            "projects.update_project",
            "projects.delete_project",
            "users_me.set_primary_company",
        }

    def test_no_membership_endpoint_is_key_writable(self, invitation_app):
        from app.api._helpers.api_key_request_auth import KEY_WRITE_BLUEPRINTS, KEY_WRITE_ENDPOINTS

        assert not KEY_WRITE_BLUEPRINTS & self.CLOSED_BLUEPRINTS
        open_endpoints = {
            r.endpoint
            for r in self._write_rules(invitation_app)
            if r.endpoint.rsplit(".", 1)[0] in KEY_WRITE_BLUEPRINTS or r.endpoint in KEY_WRITE_ENDPOINTS
        }
        assert not open_endpoints & {
            "companies.attach_user_to_company",
            "companies.boot_attached_user",
            "companies.set_member_role",
            "companies.set_join_code",
            "companies.revoke_join_code",
            "companies.set_member_grant",
            "companies.remove_member_grant",
            "companies.delete_company",
            "companies.add_member_by_phone",
            "companies.import_members",
            "invitations.create_invitation",
            "invitations.revoke_invitation",
            "projects.add_user_to_project",
            "projects.assign_project_member",
        }


# ===========================================================================
# A key owned by a platform-ops account is never honoured, nor created
# ===========================================================================


class TestApiKeyOfPlatformOpsAccount:
    def _ops_key(self, invitation_app) -> str:
        from wiring import get_container

        with invitation_app.app_context():
            created = get_container().create_api_key_usecase.execute(
                user_id=uuid.UUID(invitation_app._test_superadmin_user_id), name="ops key"
            )
        return created.token

    def test_a_key_owned_by_an_ops_account_is_unauthenticated(self, inv_client, superadmin_token, invitation_app):
        token = self._ops_key(invitation_app)
        for url in (
            "/api/v1/companies?scope=all",
            f"/api/v1/companies/{invitation_app._test_company_id}",
            "/api/v1/auth/me",
        ):
            assert inv_client.get(url, headers=_api_key_header(token)).status_code == 401, url
        # The ops account's own interactive session keeps its bypass.
        assert inv_client.get("/api/v1/companies?scope=all", headers=_auth(superadmin_token)).status_code == 200

    def test_an_ops_account_cannot_create_a_key(self, inv_client, superadmin_token):
        resp = inv_client.post("/api/v1/api-keys", json={"name": "nope"}, headers=_auth(superadmin_token))
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Forbidden"

    def test_a_key_of_an_ordinary_admin_still_works(self, inv_client, admin_token):
        headers = _admin_key(inv_client, admin_token)
        assert inv_client.get("/api/v1/companies", headers=headers).status_code == 200


# ===========================================================================
# Key use leaves a trace: one structured line per key-authenticated write
# ===========================================================================


class TestApiKeyUseIsLogged:
    def test_a_write_logs_the_key_and_never_the_credential(self, inv_client, admin_token, invitation_app, caplog):
        import logging

        created = _create_key(inv_client, admin_token, name="audited key")
        with caplog.at_level(logging.INFO, logger="folio.api_key_audit"):
            resp = inv_client.post(
                _PROJECTS_URL,
                json={"address": "2 rue du Journal"},
                headers={**_api_key_header(created["token"]), "User-Agent": "mcp-test\u00e9forged"},
            )
            assert resp.status_code == 201

        lines = [r.getMessage() for r in caplog.records if r.name == "folio.api_key_audit"]
        assert len(lines) == 1
        line = lines[0]
        assert f"key_id={created['id']}" in line
        assert f"user_id={invitation_app._test_admin_user_id}" in line
        assert "method=POST" in line and "path=/api/v1/projects" in line
        assert "remote_addr=" in line and "user_agent=mcp-test?forged" in line
        assert created["token"] not in line and "\n" not in line

    def test_a_read_and_an_interactive_write_log_nothing(self, inv_client, admin_token, caplog):
        import logging

        created = _create_key(inv_client, admin_token, name="quiet key")
        with caplog.at_level(logging.INFO, logger="folio.api_key_audit"):
            inv_client.get(_PROJECTS_URL, headers=_api_key_header(created["token"]))
            inv_client.post(_PROJECTS_URL, json={"address": "3 rue Muette"}, headers=_auth(admin_token))
        assert [r for r in caplog.records if r.name == "folio.api_key_audit"] == []
