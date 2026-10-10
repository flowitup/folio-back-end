"""Authorship alone does not keep control of a company's billing.

An admin who writes a facture or a template and is then demoted to member keeps
reading what they wrote (members see their own documents on a project's list),
but can no longer change or delete it. Once removed from the company, they lose
everything: the documents and templates stay with the company and its admins.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from tests.auth_login_helper import mint_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def author(inv_client, invitation_app):
    """A fresh admin of the test company, with a facture and a template they wrote."""
    from app import db
    from app.infrastructure.database.models import UserModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    company_id = invitation_app._test_company_id
    with invitation_app.app_context():
        user = UserModel(id=uuid4(), email=f"billing-author-{uuid4().hex[:8]}@invite-test.com", is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=user.id,
                company_id=UUID(company_id),
                role="admin",
                is_primary=True,
                attached_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        user_id, email = user.id, user.email
    token = mint_access_token(inv_client, email)

    doc = inv_client.post(
        "/api/v1/billing-documents",
        json={
            "kind": "facture",
            "recipient_name": "Client",
            "company_id": company_id,
            "items": [{"description": "Labour", "quantity": "1", "unit_price": "1000", "vat_rate": "20"}],
        },
        headers=_auth(token),
    )
    assert doc.status_code == 201, doc.get_data(as_text=True)
    tpl = inv_client.post(
        "/api/v1/billing-document-templates",
        json={"kind": "facture", "name": f"Author tpl {uuid4().hex[:8]}", "company_id": company_id},
        headers=_auth(token),
    )
    assert tpl.status_code == 201, tpl.get_data(as_text=True)
    return {"user_id": user_id, "token": token, "doc": doc.get_json(), "template": tpl.get_json()}


def _set_role(invitation_app, user_id, role):
    from app import db
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    with invitation_app.app_context():
        access = db.session.get(UserCompanyAccessModel, (user_id, UUID(invitation_app._test_company_id)))
        if role is None:
            db.session.delete(access)
        else:
            access.role = role
        db.session.commit()


def _listed(inv_client, token, path, query=None):
    resp = inv_client.get(path, query_string=query or {}, headers=_auth(token))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return [d["id"] for d in resp.get_json()["items"]]


@pytest.mark.parametrize("role", ["member", "manager"])
def test_a_demoted_author_reads_but_cannot_change_their_facture(inv_client, invitation_app, author, role):
    _set_role(invitation_app, author["user_id"], role)
    token, url = author["token"], f"/api/v1/billing-documents/{author['doc']['id']}"

    assert inv_client.get(url, headers=_auth(token)).status_code == 200
    assert author["doc"]["id"] in _listed(inv_client, token, "/api/v1/billing-documents", {"kind": "facture"})

    assert inv_client.put(url, json={"recipient_name": "Edited"}, headers=_auth(token)).status_code == 404
    assert inv_client.patch(f"{url}/status", json={"new_status": "sent"}, headers=_auth(token)).status_code == 404
    assert inv_client.delete(url, headers=_auth(token)).status_code == 404


def test_a_demoted_author_cannot_change_their_company_template(inv_client, invitation_app, author, admin_token):
    _set_role(invitation_app, author["user_id"], "member")
    token, url = author["token"], f"/api/v1/billing-document-templates/{author['template']['id']}"

    assert inv_client.get(url, headers=_auth(token)).status_code == 200
    assert inv_client.put(url, json={"name": "Hijack"}, headers=_auth(token)).status_code == 404
    assert inv_client.delete(url, headers=_auth(token)).status_code == 404
    assert inv_client.get(url, headers=_auth(admin_token)).get_json()["name"] == author["template"]["name"]


def test_a_removed_author_loses_their_facture_and_template(inv_client, invitation_app, author, admin_token):
    _set_role(invitation_app, author["user_id"], None)
    token = author["token"]
    doc_url = f"/api/v1/billing-documents/{author['doc']['id']}"
    tpl_url = f"/api/v1/billing-document-templates/{author['template']['id']}"

    assert inv_client.get(doc_url, headers=_auth(token)).status_code == 404
    assert inv_client.get(f"{doc_url}/pdf", headers=_auth(token)).status_code == 404
    assert inv_client.put(doc_url, json={"recipient_name": "Edited"}, headers=_auth(token)).status_code == 404
    assert inv_client.patch(f"{doc_url}/status", json={"new_status": "paid"}, headers=_auth(token)).status_code == 404
    assert inv_client.delete(doc_url, headers=_auth(token)).status_code == 404
    assert author["doc"]["id"] not in _listed(inv_client, token, "/api/v1/billing-documents", {"kind": "facture"})

    assert inv_client.get(tpl_url, headers=_auth(token)).status_code == 404
    assert inv_client.put(tpl_url, json={"name": "Hijack"}, headers=_auth(token)).status_code == 404
    assert inv_client.delete(tpl_url, headers=_auth(token)).status_code == 404
    assert author["template"]["id"] not in _listed(inv_client, token, "/api/v1/billing-document-templates")

    # The company keeps both, untouched.
    kept = inv_client.get(doc_url, headers=_auth(admin_token))
    assert kept.status_code == 200
    assert kept.get_json()["recipient_name"] == "Client"
    assert kept.get_json()["status"] == "draft"
    assert inv_client.get(tpl_url, headers=_auth(admin_token)).status_code == 200


def test_an_author_still_admin_keeps_full_control(inv_client, author):
    token, url = author["token"], f"/api/v1/billing-documents/{author['doc']['id']}"
    assert inv_client.put(url, json={"recipient_name": "Edited"}, headers=_auth(token)).status_code == 200
    assert inv_client.patch(f"{url}/status", json={"new_status": "sent"}, headers=_auth(token)).status_code == 200
    assert inv_client.delete(url, headers=_auth(token)).status_code == 204
