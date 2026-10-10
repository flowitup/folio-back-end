"""GET /api/v1/users?q= — the add-member lookup only returns users of the caller's companies."""

from __future__ import annotations

# Fixtures from conftest.py: invitation_app seeds one company holding admin, member and
# target; outsider is attached to no company; superadmin is platform ops.


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _emails(client, token: str, q: str = "invite-test") -> set[str]:
    resp = client.get("/api/v1/users", query_string={"q": q}, headers=_auth(token))
    assert resp.status_code == 200
    return {u["email"] for u in resp.get_json()["users"]}


def test_company_member_sees_only_users_of_their_company(inv_client, member_token):
    emails = _emails(inv_client, member_token)
    assert {"admin@invite-test.com", "member@invite-test.com", "target@invite-test.com"} <= emails
    assert "outsider@invite-test.com" not in emails
    assert "superadmin@invite-test.com" not in emails


def test_company_admin_cannot_find_users_outside_their_companies(inv_client, admin_token):
    assert "outsider@invite-test.com" not in _emails(inv_client, admin_token)
    assert _emails(inv_client, admin_token, q="outsider") == set()


def test_user_without_company_finds_nobody(inv_client, outsider_token):
    assert _emails(inv_client, outsider_token) == set()


def test_platform_ops_search_is_not_restricted(inv_client, superadmin_token):
    emails = _emails(inv_client, superadmin_token)
    assert {"outsider@invite-test.com", "admin@invite-test.com"} <= emails


def test_there_is_no_unauthenticated_user_lookup_by_id(inv_client, invitation_app):
    """The old `GET /users/<id>` stub answered anonymous callers (501, echoing the id); it is gone."""
    resp = inv_client.get(f"/api/v1/users/{invitation_app._test_member_user_id}")
    assert resp.status_code == 404
    assert invitation_app._test_member_user_id not in resp.get_data(as_text=True)
