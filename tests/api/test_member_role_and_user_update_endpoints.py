"""Integration tests for member role change + user profile update.

- PATCH /api/v1/projects/<project_id>/members/<user_id>  (change project role)
- PATCH /api/v1/admin/users/<user_id>                    (edit email / display name)

Fixtures from conftest.py: inv_client, superadmin_token, member_token, admin_token,
invitation_app (seeds project P1 owned by admin_user; target_user is a member of P1
with member_role; member_user is a member of P1).
"""

from __future__ import annotations

from uuid import uuid4


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# PATCH /projects/<pid>/members/<uid> — deprecated: role_id is accepted and ignored
# ---------------------------------------------------------------------------


class TestUpdateMemberRole:
    """Per-project roles are gone; the endpoint stays as a tolerant no-op stub."""

    def _url(self, app, uid: str) -> str:
        return f"/api/v1/projects/{app._test_project_id}/members/{uid}"

    def test_200_accepts_and_ignores_role_id(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app, invitation_app._test_target_user_id),
            json={"role_id": str(uuid4())},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["deprecated"] is True

    def test_200_without_a_body(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app, invitation_app._test_target_user_id),
            json={},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200

    def test_403_caller_lacks_manage_users(self, inv_client, member_token, invitation_app):
        """A company member holds no project:manage_users, on this project or any other."""
        resp = inv_client.patch(
            self._url(invitation_app, invitation_app._test_target_user_id),
            json={"role_id": str(uuid4())},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_404_target_not_a_member(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app, str(uuid4())),
            json={"role_id": str(uuid4())},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 404

    def test_401_unauthenticated(self, inv_client, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app, invitation_app._test_target_user_id),
            json={"role_id": str(uuid4())},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /admin/users/<uid>
# ---------------------------------------------------------------------------


class TestUpdateUser:
    def _url(self, uid: str) -> str:
        return f"/api/v1/admin/users/{uid}"

    def test_200_updates_display_name(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_member_user_id),
            json={"display_name": "Renamed Member"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["display_name"] == "Renamed Member"

    def test_200_email_normalized_lowercase(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_target_user_id),
            json={"email": "Renamed.Target@Invite-Test.com"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["email"] == "renamed.target@invite-test.com"

    def test_409_duplicate_email(self, inv_client, superadmin_token, invitation_app):
        # member@invite-test.com belongs to member_user; assigning it to admin_user collides
        resp = inv_client.patch(
            self._url(invitation_app._test_admin_user_id),
            json={"email": invitation_app._test_member_email},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 409

    def test_403_non_superadmin(self, inv_client, member_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_target_user_id),
            json={"display_name": "x"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_404_unknown_user(self, inv_client, superadmin_token):
        resp = inv_client.patch(
            self._url(str(uuid4())),
            json={"display_name": "x"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 404

    def test_400_empty_body(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_target_user_id),
            json={},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code == 400

    def test_422_invalid_email(self, inv_client, superadmin_token, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_target_user_id),
            json={"email": "not-an-email"},
            headers=_auth(superadmin_token),
        )
        assert resp.status_code in (400, 422)

    def test_401_unauthenticated(self, inv_client, invitation_app):
        resp = inv_client.patch(
            self._url(invitation_app._test_target_user_id),
            json={"display_name": "x"},
        )
        assert resp.status_code == 401
