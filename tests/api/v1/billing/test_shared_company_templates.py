"""A company's billing templates are shared with every admin of that company.

The list is company-scoped, so every per-template action must follow the same
rule as company billing documents: the author or any company admin may open,
edit, apply and delete a company template, while a plain member of the same
company and an admin of another company still get 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from tests.auth_login_helper import mint_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def template_users(invitation_app):
    """A second admin of the test company, and the admin of another company."""
    from app import db
    from app.infrastructure.database.models import UserModel
    from app.infrastructure.database.models.company import CompanyModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        co_admin = UserModel(id=uuid4(), email="tpl-co-admin@invite-test.com", is_active=True)
        foreign_admin = UserModel(id=uuid4(), email="tpl-foreign-admin@invite-test.com", is_active=True)
        db.session.add_all([co_admin, foreign_admin])
        db.session.flush()
        foreign_company = CompanyModel(
            id=uuid4(),
            legal_name="Template Outsider SARL",
            address="3 rue Ailleurs",
            created_by=foreign_admin.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(foreign_company)
        db.session.flush()
        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=co_admin.id,
                    company_id=UUID(invitation_app._test_company_id),
                    role="admin",
                    is_primary=True,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=foreign_admin.id,
                    company_id=foreign_company.id,
                    role="admin",
                    is_primary=True,
                    attached_at=now,
                ),
            ]
        )
        db.session.commit()
        return {"co_admin": co_admin.email, "foreign_admin": foreign_admin.email}


@pytest.fixture
def co_admin_token(inv_client, template_users):
    return mint_access_token(inv_client, template_users["co_admin"])


@pytest.fixture
def foreign_admin_token(inv_client, template_users):
    return mint_access_token(inv_client, template_users["foreign_admin"])


@pytest.fixture
def company_template(inv_client, admin_token, invitation_app):
    """A template the company admin creates in the test company."""
    resp = inv_client.post(
        "/api/v1/billing-document-templates",
        json={
            "kind": "devis",
            "name": f"Shared {uuid4().hex[:8]}",
            "items": [{"description": "Labour", "quantity": "2", "unit_price": "150", "vat_rate": "20"}],
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    tpl = resp.get_json()
    assert tpl["company_id"] == invitation_app._test_company_id
    return tpl


def test_another_admin_of_the_company_opens_edits_and_uses_the_template(
    inv_client, co_admin_token, company_template, invitation_app
):
    url = f"/api/v1/billing-document-templates/{company_template['id']}"

    listed = inv_client.get("/api/v1/billing-document-templates", headers=_auth(co_admin_token))
    assert company_template["id"] in [t["id"] for t in listed.get_json()["items"]]

    got = inv_client.get(url, headers=_auth(co_admin_token))
    assert got.status_code == 200, got.get_data(as_text=True)

    updated = inv_client.put(url, json={"name": company_template["name"] + " v2"}, headers=_auth(co_admin_token))
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["name"] == company_template["name"] + " v2"
    # The author stays the author.
    assert updated.get_json()["user_id"] == company_template["user_id"]

    applied = inv_client.post(
        f"/api/v1/billing-documents/from-template/{company_template['id']}",
        json={"recipient_name": "Client", "company_id": invitation_app._test_company_id},
        headers=_auth(co_admin_token),
    )
    assert applied.status_code == 201, applied.get_data(as_text=True)
    assert len(applied.get_json()["items"]) == 1


def test_another_admin_of_the_company_deletes_the_template(inv_client, co_admin_token, admin_token, company_template):
    url = f"/api/v1/billing-document-templates/{company_template['id']}"
    resp = inv_client.delete(url, headers=_auth(co_admin_token))
    assert resp.status_code == 204
    assert inv_client.get(url, headers=_auth(admin_token)).status_code == 404


def test_a_plain_member_of_the_company_cannot_reach_the_template(inv_client, member_token, company_template):
    url = f"/api/v1/billing-document-templates/{company_template['id']}"
    assert inv_client.get(url, headers=_auth(member_token)).status_code == 404
    assert inv_client.put(url, json={"name": "Hijack"}, headers=_auth(member_token)).status_code == 404
    assert inv_client.delete(url, headers=_auth(member_token)).status_code == 404


def test_an_admin_of_another_company_cannot_reach_the_template(
    inv_client, foreign_admin_token, admin_token, company_template
):
    url = f"/api/v1/billing-document-templates/{company_template['id']}"
    assert inv_client.get(url, headers=_auth(foreign_admin_token)).status_code == 404
    assert inv_client.put(url, json={"name": "Hijack"}, headers=_auth(foreign_admin_token)).status_code == 404
    assert inv_client.delete(url, headers=_auth(foreign_admin_token)).status_code == 404
    applied = inv_client.post(
        f"/api/v1/billing-documents/from-template/{company_template['id']}",
        json={"recipient_name": "Client"},
        headers=_auth(foreign_admin_token),
    )
    assert applied.status_code == 404
    listed = inv_client.get("/api/v1/billing-document-templates", headers=_auth(foreign_admin_token))
    assert company_template["id"] not in [t["id"] for t in listed.get_json()["items"]]
    # Still there for its company.
    assert inv_client.get(url, headers=_auth(admin_token)).status_code == 200
