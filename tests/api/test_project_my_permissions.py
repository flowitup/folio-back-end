"""Project responses expose the caller's EFFECTIVE per-project permissions.

`my_permissions` = global-role perms UNION the caller's membership-role perms on
that project. The frontend gates per-project UI (e.g. "log labor") on this, so an
invited project admin/manager gets the right UI even though their global role is
the read-only default.

Fixtures (conftest): inv_client, superadmin_token, invitation_app.

The membership-union math (global ∪ membership-role perms) is covered by
tests/unit/api/test_project_membership_role_permissions.py; here we assert the
project endpoints actually expose it as `my_permissions`.
"""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_detail_includes_my_permissions(inv_client, superadmin_token, invitation_app):
    pid = invitation_app._test_project_id
    resp = inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(superadmin_token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["my_permissions"], list)
    assert "*:*" in body["my_permissions"]


def test_list_includes_my_permissions(inv_client, superadmin_token):
    resp = inv_client.get("/api/v1/projects", headers=_auth(superadmin_token))
    assert resp.status_code == 200
    projects = resp.get_json()["projects"]
    assert projects, "expected at least one project"
    for p in projects:
        assert "my_permissions" in p
        assert "*:*" in p["my_permissions"]
