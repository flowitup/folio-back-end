"""Platform-ops gates: `users.is_platform_ops`, never a role and never the token.

Support-only surfaces (admin/*, person merge, cross-company company listing)
answer to the flag alone; a company admin is refused there. Because the flag is
read from the database on every request, revoking it applies to the token the
caller already holds.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ops_off(invitation_app):
    """Temporarily clear the ops flag on the fixture's ops user."""
    from app import db
    from app.infrastructure.database.models import UserModel

    with invitation_app.app_context():
        user = db.session.get(UserModel, UUID(invitation_app._test_superadmin_user_id))
        user.is_platform_ops = False
        db.session.commit()
    yield
    with invitation_app.app_context():
        user = db.session.get(UserModel, UUID(invitation_app._test_superadmin_user_id))
        user.is_platform_ops = True
        db.session.commit()


class TestAdminRoutes:
    def _url(self, invitation_app) -> str:
        return f"/api/v1/admin/users/{invitation_app._test_target_user_id}/memberships"

    def _body(self, invitation_app) -> dict:
        return {"project_ids": [invitation_app._test_project_2_id]}

    def test_ops_may_bulk_add(self, inv_client, invitation_app, superadmin_token):
        resp = inv_client.post(
            self._url(invitation_app), json=self._body(invitation_app), headers=_auth(superadmin_token)
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_company_admin_may_not(self, inv_client, invitation_app, admin_token):
        resp = inv_client.post(self._url(invitation_app), json=self._body(invitation_app), headers=_auth(admin_token))
        assert resp.status_code == 403

    def test_flag_revoked_applies_to_the_same_token(self, inv_client, invitation_app, superadmin_token, ops_off):
        """The token is unchanged; only the DB row flipped — and access is gone."""
        resp = inv_client.post(
            self._url(invitation_app), json=self._body(invitation_app), headers=_auth(superadmin_token)
        )
        assert resp.status_code == 403

    def test_user_search_is_ops_only(self, inv_client, admin_token, superadmin_token):
        assert inv_client.get("/api/v1/admin/users?q=target", headers=_auth(admin_token)).status_code == 403
        assert inv_client.get("/api/v1/admin/users?q=target", headers=_auth(superadmin_token)).status_code == 200


class TestPersonSurfaces:
    def test_company_admin_is_refused(self, inv_client, admin_token):
        resp = inv_client.post(
            f"/api/v1/persons/{uuid4()}/merge",
            json={"target_person_id": str(uuid4())},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403

    def test_member_never_reaches_the_person_directory(self, inv_client, member_token):
        """A member sees the day roster of their project, never the global directory."""
        assert inv_client.get("/api/v1/persons?q=target", headers=_auth(member_token)).status_code == 403


class TestCompanyListing:
    def test_scope_all_is_ops_only(self, inv_client, admin_token, superadmin_token):
        assert inv_client.get("/api/v1/companies?scope=all", headers=_auth(admin_token)).status_code == 403
        assert inv_client.get("/api/v1/companies?scope=all", headers=_auth(superadmin_token)).status_code == 200


class TestAuthMe:
    def test_exposes_the_flag_and_resolved_permissions(self, inv_client, admin_token, superadmin_token):
        admin_me = inv_client.get("/api/v1/auth/me", headers=_auth(admin_token))
        assert admin_me.status_code == 200
        body = admin_me.get_json()
        assert body["is_platform_ops"] is False
        assert "project:create" in body["permissions"]
        assert "roles" not in body  # a user carries no roles of their own
        assert [c["role"] for c in body["companies"]] == ["admin"]

        ops_me = inv_client.get("/api/v1/auth/me", headers=_auth(superadmin_token)).get_json()
        assert ops_me["is_platform_ops"] is True
        assert ops_me["permissions"] == ["*:*"]

    def test_member_sees_no_write_permission(self, inv_client, member_token):
        body = inv_client.get("/api/v1/auth/me", headers=_auth(member_token)).get_json()
        assert "project:read" in body["permissions"]
        assert "project:manage_invoices" not in body["permissions"]
        assert "project:create" not in body["permissions"]


class TestRemovedStubs:
    """The deprecated shims Phase 3 kept for released clients are gone."""

    def test_roles_endpoint_is_gone(self, inv_client, admin_token):
        assert inv_client.get("/api/v1/roles", headers=_auth(admin_token)).status_code == 404

    def test_patch_member_role_is_gone(self, inv_client, invitation_app, admin_token):
        resp = inv_client.patch(
            f"/api/v1/projects/{invitation_app._test_project_id}/members/{invitation_app._test_member_user_id}",
            json={},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404
