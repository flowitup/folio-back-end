"""A company admin reads and links billing documents on every project of their company.

Company admins hold project:read on all company projects without an assignment
(app.domain.authz.matrix), so the billing project check must let them through,
while an unassigned member of the same company and an admin of another company
are still refused.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def unassigned_projects(invitation_app):
    """A project of the admin's company the admin is not assigned to, and one of another company."""
    from app import db
    from app.infrastructure.database.models import ProjectModel, UserModel
    from app.infrastructure.database.models.company import CompanyModel

    with invitation_app.app_context():
        member = db.session.query(UserModel).filter_by(email=invitation_app._test_member_email).one()
        now = datetime.now(timezone.utc)
        other_company = CompanyModel(
            id=uuid4(),
            legal_name="Other Tenant SARL",
            address="2 rue Ailleurs",
            created_by=member.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(other_company)
        db.session.flush()
        own = ProjectModel(
            name="Unassigned company project",
            owner_id=member.id,
            company_id=UUID(invitation_app._test_company_id),
        )
        foreign = ProjectModel(name="Other tenant project", owner_id=member.id, company_id=other_company.id)
        db.session.add_all([own, foreign])
        db.session.commit()
        return {"own": str(own.id), "foreign": str(foreign.id)}


def test_company_admin_lists_billing_of_unassigned_company_project(inv_client, admin_token, unassigned_projects):
    resp = inv_client.get(
        f"/api/v1/projects/{unassigned_projects['own']}/billing-documents", headers=_auth(admin_token)
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_company_admin_filters_billing_list_by_unassigned_company_project(inv_client, admin_token, unassigned_projects):
    resp = inv_client.get(
        "/api/v1/billing-documents",
        query_string={"kind": "devis", "project_id": unassigned_projects["own"]},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_company_admin_links_devis_to_unassigned_company_project(
    inv_client, admin_token, invitation_app, unassigned_projects
):
    resp = inv_client.post(
        "/api/v1/billing-documents",
        json={
            "kind": "devis",
            "recipient_name": "Client",
            "company_id": invitation_app._test_company_id,
            "project_id": unassigned_projects["own"],
            "items": [{"description": "Labour", "quantity": "1", "unit_price": "100", "vat_rate": "20"}],
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["project_id"] == unassigned_projects["own"]


def test_company_admin_is_refused_on_another_company_project(inv_client, admin_token, unassigned_projects):
    resp = inv_client.get(
        f"/api/v1/projects/{unassigned_projects['foreign']}/billing-documents", headers=_auth(admin_token)
    )
    assert resp.status_code == 403
    listed = inv_client.get(
        "/api/v1/billing-documents",
        query_string={"kind": "devis", "project_id": unassigned_projects["foreign"]},
        headers=_auth(admin_token),
    )
    assert listed.status_code == 403


def test_assigned_member_does_not_see_the_company_billing_of_the_project(
    inv_client, admin_token, member_token, invitation_app
):
    """Billing amounts stay with the company admin: an assigned member lists only their own documents."""
    from app import db
    from app.infrastructure.database.models.associations import user_projects

    project_id = invitation_app._test_project_3_id
    with invitation_app.app_context():
        db.session.execute(
            user_projects.insert().values(
                user_id=UUID(invitation_app._test_member_user_id), project_id=UUID(project_id)
            )
        )
        db.session.commit()
    created = inv_client.post(
        "/api/v1/billing-documents",
        json={
            "kind": "facture",
            "recipient_name": "Client of the company",
            "company_id": invitation_app._test_company_id,
            "project_id": project_id,
            "items": [{"description": "Works", "quantity": "1", "unit_price": "3000", "vat_rate": "20"}],
        },
        headers=_auth(admin_token),
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    doc_id = created.get_json()["id"]

    as_member = inv_client.get(f"/api/v1/projects/{project_id}/billing-documents", headers=_auth(member_token))
    assert as_member.status_code == 200, as_member.get_data(as_text=True)
    assert doc_id not in {d["id"] for d in as_member.get_json()["billing_documents"]}

    as_admin = inv_client.get(f"/api/v1/projects/{project_id}/billing-documents", headers=_auth(admin_token))
    assert doc_id in {d["id"] for d in as_admin.get_json()["billing_documents"]}
