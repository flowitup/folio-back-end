"""GET /api/v1/users?q= — company admins and managers find users of their own companies; platform ops find anyone."""

from __future__ import annotations

from uuid import UUID

# Fixtures from conftest.py: invitation_app seeds one company holding admin, member and
# target; outsider is attached to no company; superadmin is platform ops.


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _emails(client, token: str, q: str = "invite-test") -> set[str]:
    resp = client.get("/api/v1/users", query_string={"q": q}, headers=_auth(token))
    assert resp.status_code == 200
    return {u["email"] for u in resp.get_json()["users"]}


def test_company_admin_sees_only_users_of_their_company(inv_client, admin_token):
    emails = _emails(inv_client, admin_token)
    assert {"admin@invite-test.com", "member@invite-test.com", "target@invite-test.com"} <= emails
    assert "outsider@invite-test.com" not in emails
    assert "superadmin@invite-test.com" not in emails


def test_plain_company_member_cannot_list_colleagues(inv_client, member_token):
    resp = inv_client.get("/api/v1/users", query_string={"q": "invite-test"}, headers=_auth(member_token))
    assert resp.status_code == 403
    assert "admin@invite-test.com" not in resp.get_data(as_text=True)


def test_company_manager_finds_users_of_their_company(inv_client, member_token, invitation_app):
    from app import db
    from app.infrastructure.database.models import UserCompanyAccessModel

    with invitation_app.app_context():
        row = (
            db.session.query(UserCompanyAccessModel).filter_by(user_id=UUID(invitation_app._test_member_user_id)).one()
        )
        row.role = "manager"
        db.session.commit()
    try:
        assert "admin@invite-test.com" in _emails(inv_client, member_token)
    finally:
        with invitation_app.app_context():
            row = (
                db.session.query(UserCompanyAccessModel)
                .filter_by(user_id=UUID(invitation_app._test_member_user_id))
                .one()
            )
            row.role = "member"
            db.session.commit()


def test_phone_placeholder_emails_are_neither_matched_nor_returned(
    inv_client, admin_token, superadmin_token, invitation_app
):
    """The placeholder address of a phone sign-up spells the number: it must not leak or be probed."""
    from app import db
    from app.infrastructure.database.models import UserCompanyAccessModel, UserModel

    email = "phone-33611223344@no-email.folio.flowitup.com"
    with invitation_app.app_context():
        user = UserModel(email=email, phone="+33611223344", is_active=True)
        db.session.add(user)
        db.session.flush()
        company_id = (
            db.session.query(UserCompanyAccessModel.company_id)
            .filter_by(user_id=UUID(invitation_app._test_admin_user_id))
            .scalar()
        )
        db.session.add(UserCompanyAccessModel(user_id=user.id, company_id=company_id, role="member"))
        db.session.commit()
        user_id = user.id
    try:
        for token in (admin_token, superadmin_token):
            for q in ("phone-33", "33611", "no-email", email):
                assert _emails(inv_client, token, q=q) == set()
            assert not any("no-email" in e for e in _emails(inv_client, token, q="invite-test"))
    finally:
        with invitation_app.app_context():
            db.session.query(UserCompanyAccessModel).filter_by(user_id=user_id).delete()
            db.session.query(UserModel).filter_by(id=user_id).delete()
            db.session.commit()


def test_company_admin_cannot_find_users_outside_their_companies(inv_client, admin_token):
    assert "outsider@invite-test.com" not in _emails(inv_client, admin_token)
    assert _emails(inv_client, admin_token, q="outsider") == set()


def test_user_without_company_finds_nobody(inv_client, outsider_token):
    resp = inv_client.get("/api/v1/users", query_string={"q": "invite-test"}, headers=_auth(outsider_token))
    assert resp.status_code == 403


def test_platform_ops_search_is_not_restricted(inv_client, superadmin_token):
    emails = _emails(inv_client, superadmin_token)
    assert {"outsider@invite-test.com", "admin@invite-test.com"} <= emails


def test_there_is_no_unauthenticated_user_lookup_by_id(inv_client, invitation_app):
    """The old `GET /users/<id>` stub answered anonymous callers (501, echoing the id); it is gone."""
    resp = inv_client.get(f"/api/v1/users/{invitation_app._test_member_user_id}")
    assert resp.status_code == 404
    assert invitation_app._test_member_user_id not in resp.get_data(as_text=True)
