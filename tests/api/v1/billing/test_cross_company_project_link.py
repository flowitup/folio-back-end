"""A facture linked to a project of another company never moves money into it.

A subcontractor who really belongs to a project of another company may still link their own
company's documents to it, but the paid facture must not inject a released_funds row there (the
project's own admins could not remove it), and a creator who was unassigned cannot link at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def projects(invitation_app):
    """A project of another company the admin is assigned to, one they only created, one of their company."""
    from app import db
    from app.infrastructure.database.models import ProjectModel
    from app.infrastructure.database.models.associations import user_projects
    from app.infrastructure.database.models.company import CompanyModel

    admin_id = UUID(invitation_app._test_admin_user_id)
    with invitation_app.app_context():
        now = datetime.now(timezone.utc)
        other_company = CompanyModel(
            id=uuid4(),
            legal_name="Main Contractor SARL",
            address="2 rue Ailleurs",
            created_by=UUID(invitation_app._test_member_user_id),
            created_at=now,
            updated_at=now,
        )
        db.session.add(other_company)
        db.session.flush()
        assigned = ProjectModel(name="Assigned abroad", owner_id=UUID(invitation_app._test_member_user_id))
        assigned.company_id = other_company.id
        created_only = ProjectModel(name="Created then unassigned", owner_id=admin_id, company_id=other_company.id)
        own = ProjectModel(name="Own company site", owner_id=admin_id, company_id=UUID(invitation_app._test_company_id))
        db.session.add_all([assigned, created_only, own])
        db.session.flush()
        db.session.execute(user_projects.insert().values(user_id=admin_id, project_id=assigned.id))
        db.session.commit()
        return {"assigned": str(assigned.id), "created_only": str(created_only.id), "own": str(own.id)}


def _facture(inv_client, token, invitation_app, project_id):
    return inv_client.post(
        "/api/v1/billing-documents",
        json={
            "kind": "facture",
            "recipient_name": "Client",
            "company_id": invitation_app._test_company_id,
            "project_id": project_id,
            "items": [{"description": "Works", "quantity": "1", "unit_price": "1500", "vat_rate": "0"}],
        },
        headers=_auth(token),
    )


def _pay(inv_client, token, doc_id):
    for status in ("sent", "paid"):
        resp = inv_client.patch(
            f"/api/v1/billing-documents/{doc_id}/status", json={"new_status": status}, headers=_auth(token)
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)


def _releases(invitation_app, project_id):
    from app import db
    from app.infrastructure.database.models.invoice import InvoiceModel

    with invitation_app.app_context():
        return db.session.query(InvoiceModel).filter_by(project_id=UUID(project_id), type="released_funds").count()


def test_paid_facture_releases_funds_into_its_own_companys_project(inv_client, admin_token, invitation_app, projects):
    created = _facture(inv_client, admin_token, invitation_app, projects["own"])
    assert created.status_code == 201, created.get_data(as_text=True)
    _pay(inv_client, admin_token, created.get_json()["id"])
    assert _releases(invitation_app, projects["own"]) == 1


def test_paid_facture_of_another_companys_project_releases_nothing(inv_client, admin_token, invitation_app, projects):
    created = _facture(inv_client, admin_token, invitation_app, projects["assigned"])
    assert created.status_code == 201, created.get_data(as_text=True)
    doc_id = created.get_json()["id"]
    _pay(inv_client, admin_token, doc_id)
    assert _releases(invitation_app, projects["assigned"]) == 0

    edited = inv_client.put(
        f"/api/v1/billing-documents/{doc_id}",
        json={"recipient_name": "Renamed after paid"},
        headers=_auth(admin_token),
    )
    assert edited.status_code == 200, edited.get_data(as_text=True)
    assert _releases(invitation_app, projects["assigned"]) == 0


def test_relinking_a_paid_facture_to_another_companys_project_removes_its_release(
    inv_client, admin_token, invitation_app, projects
):
    created = _facture(inv_client, admin_token, invitation_app, projects["own"])
    doc_id = created.get_json()["id"]
    _pay(inv_client, admin_token, doc_id)
    before = _releases(invitation_app, projects["own"])
    moved = inv_client.put(
        f"/api/v1/billing-documents/{doc_id}", json={"project_id": projects["assigned"]}, headers=_auth(admin_token)
    )
    assert moved.status_code == 200, moved.get_data(as_text=True)
    assert _releases(invitation_app, projects["own"]) == before - 1
    assert _releases(invitation_app, projects["assigned"]) == 0


def test_unassigned_creator_cannot_link_a_document_to_the_project(inv_client, admin_token, invitation_app, projects):
    """owner_id alone is no membership: the creator was unassigned from the project."""
    resp = _facture(inv_client, admin_token, invitation_app, projects["created_only"])
    assert resp.status_code == 403, resp.get_data(as_text=True)
