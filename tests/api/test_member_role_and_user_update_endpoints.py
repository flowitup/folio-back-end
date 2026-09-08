"""Integration tests for the user profile update endpoint.

- PATCH /api/v1/admin/users/<user_id>  (edit email / display name)

The per-project role endpoint it used to cover is gone: a project assignment
carries no role, and a company role changes through
PATCH /companies/<id>/access/<uid>/role.

Fixtures from conftest.py: inv_client, superadmin_token, member_token, admin_token,
invitation_app (seeds project P1 owned by admin_user; target_user and member_user
are assigned to P1).
"""

from __future__ import annotations

from uuid import uuid4


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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
