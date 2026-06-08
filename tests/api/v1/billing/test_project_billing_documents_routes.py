"""API integration tests for the project-scoped billing documents endpoint.

Covers:
  - GET /api/v1/projects/<id>/billing-documents (list, access control)
  - Create with project_id → project_id present in response
  - PUT with {"project_id": null} → unlinks the doc
  - PUT with a new project_id → links to a different project
  - Non-member → 403
  - Doc NOT linked to project excluded from listing
"""

from __future__ import annotations

import uuid

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_ITEM = {"description": "Labour", "quantity": "2", "unit_price": "300", "vat_rate": "10"}


def _create_body(company_id: str, project_id: str | None = None) -> dict:
    body = {
        "kind": "devis",
        "recipient_name": "Test Client",
        "company_id": company_id,
        "items": [_ITEM],
    }
    if project_id is not None:
        body["project_id"] = project_id
    return body


def _facture_body(company_id: str, project_id: str | None = None) -> dict:
    body = {
        "kind": "facture",
        "recipient_name": "Test Client",
        "company_id": company_id,
        "items": [_ITEM],
        "payment_due_date": "2026-12-31",
    }
    if project_id is not None:
        body["project_id"] = project_id
    return body


# ---------------------------------------------------------------------------
# Module-scoped project owned by the billing_token (superadmin) user.
# The pre-seeded test_project is owned by admin@, not superadmin@, so we
# create a dedicated project here to avoid cross-ownership failures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def billing_project_id(_billing_client, billing_token):
    """Create a project owned by the billing_token (superadmin) user."""
    resp = _billing_client.post(
        "/api/v1/projects",
        json={"name": "Billing Test Project"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


@pytest.fixture(scope="module")
def billing_project2_id(_billing_client, billing_token):
    """Second project owned by billing_token for relink tests."""
    resp = _billing_client.post(
        "/api/v1/projects",
        json={"name": "Billing Test Project 2"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Create with project_id
# ---------------------------------------------------------------------------


class TestCreateWithProjectId:
    def test_create_devis_with_project_id_returns_201(
        self, inv_client, billing_token, billing_profile, billing_project_id
    ):
        resp = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["project_id"] == billing_project_id

    def test_create_facture_with_project_id_returns_201(
        self, inv_client, billing_token, billing_profile, billing_project_id
    ):
        resp = inv_client.post(
            "/api/v1/billing-documents",
            json=_facture_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["project_id"] == billing_project_id
        assert data["kind"] == "facture"


# ---------------------------------------------------------------------------
# GET /projects/<id>/billing-documents
# ---------------------------------------------------------------------------


class TestListProjectBillingDocuments:
    def test_returns_linked_devis_and_facture(self, inv_client, billing_token, billing_profile, billing_project_id):
        # Link one devis and one facture to the project
        r1 = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r1.status_code == 201
        devis_id = r1.get_json()["id"]

        r2 = inv_client.post(
            "/api/v1/billing-documents",
            json=_facture_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r2.status_code == 201
        facture_id = r2.get_json()["id"]

        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert "billing_documents" in data
        ids = [d["id"] for d in data["billing_documents"]]
        assert devis_id in ids
        assert facture_id in ids

    def test_response_shape_has_expected_fields(self, inv_client, billing_token, billing_profile, billing_project_id):
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        items = resp.get_json()["billing_documents"]
        assert len(items) >= 1
        first = next(d for d in items if d["id"] == r.get_json()["id"])
        for field in (
            "id",
            "kind",
            "document_number",
            "status",
            "issue_date",
            "recipient_name",
            "total_ht",
            "total_ttc",
        ):
            assert field in first, f"Missing field: {field}"

    def test_amounts_are_correct(self, inv_client, billing_token, billing_profile, billing_project_id):
        """total_ht = 2 * 300 = 600; total_ttc = 600 * 1.10 = 660."""
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        doc_id = r.get_json()["id"]

        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        item = next(d for d in resp.get_json()["billing_documents"] if d["id"] == doc_id)
        assert float(item["total_ht"]) == 600.0
        assert float(item["total_ttc"]) == 660.0

    def test_doc_not_linked_to_project_is_excluded(
        self, inv_client, billing_token, billing_profile, billing_project_id
    ):
        # Create a doc WITHOUT project_id
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], None),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        unlinked_id = r.get_json()["id"]

        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.get_json()["billing_documents"]]
        assert unlinked_id not in ids

    def test_unauthenticated_returns_401(self, inv_client, billing_project_id):
        resp = inv_client.get(f"/api/v1/projects/{billing_project_id}/billing-documents")
        assert resp.status_code == 401

    def test_non_member_returns_403(self, inv_client, other_token, billing_project_id):
        """other_token user has no access to billing_project_id (owned by superadmin)."""
        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(other_token),
        )
        assert resp.status_code == 403

    def test_nonexistent_project_returns_400(self, inv_client, billing_token):
        new_project_id = str(uuid.uuid4())
        resp = inv_client.get(
            f"/api/v1/projects/{new_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        # assert_project_read_access raises ValueError → 400
        assert resp.status_code == 400

    def test_invalid_project_uuid_returns_400(self, inv_client, billing_token):
        resp = inv_client.get(
            "/api/v1/projects/not-a-uuid/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 400

    def test_project_owner_can_access(self, inv_client, billing_token, billing_project_id):
        """billing_token user owns billing_project_id → should see the list."""
        resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PUT unlink / relink (project_id update tri-state)
# ---------------------------------------------------------------------------


class TestUpdateProjectIdUnlinkRelink:
    def test_put_project_id_null_clears_link(self, inv_client, billing_token, billing_profile, billing_project_id):
        # Create with project_id
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        doc_id = r.get_json()["id"]

        # PUT with explicit null to unlink
        put_resp = inv_client.put(
            f"/api/v1/billing-documents/{doc_id}",
            json={"project_id": None},
            headers=_auth(billing_token),
        )
        assert put_resp.status_code == 200, put_resp.get_data(as_text=True)
        assert put_resp.get_json()["project_id"] is None

        # Verify no longer in project list
        list_resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert list_resp.status_code == 200
        ids = [d["id"] for d in list_resp.get_json()["billing_documents"]]
        assert doc_id not in ids

    def test_put_new_project_id_sets_link(
        self, inv_client, billing_token, billing_profile, billing_project_id, billing_project2_id
    ):
        # Create linked to project 1
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        doc_id = r.get_json()["id"]

        # PUT to change to project 2
        put_resp = inv_client.put(
            f"/api/v1/billing-documents/{doc_id}",
            json={"project_id": billing_project2_id},
            headers=_auth(billing_token),
        )
        assert put_resp.status_code == 200
        assert put_resp.get_json()["project_id"] == billing_project2_id

        # Should appear in project 2 list
        list_resp = inv_client.get(
            f"/api/v1/projects/{billing_project2_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert list_resp.status_code == 200
        ids = [d["id"] for d in list_resp.get_json()["billing_documents"]]
        assert doc_id in ids

    def test_put_without_project_id_field_does_not_change_link(
        self, inv_client, billing_token, billing_profile, billing_project_id
    ):
        """Omitting project_id from PUT body must NOT unlink the document (tri-state)."""
        # Create linked
        r = inv_client.post(
            "/api/v1/billing-documents",
            json=_create_body(billing_profile["company_id"], billing_project_id),
            headers=_auth(billing_token),
        )
        assert r.status_code == 201
        doc_id = r.get_json()["id"]

        # PUT without project_id field
        put_resp = inv_client.put(
            f"/api/v1/billing-documents/{doc_id}",
            json={"recipient_name": "Updated Name"},
            headers=_auth(billing_token),
        )
        assert put_resp.status_code == 200
        # project_id must still be set
        assert put_resp.get_json()["project_id"] == billing_project_id

        # Still appears in project list
        list_resp = inv_client.get(
            f"/api/v1/projects/{billing_project_id}/billing-documents",
            headers=_auth(billing_token),
        )
        assert list_resp.status_code == 200
        ids = [d["id"] for d in list_resp.get_json()["billing_documents"]]
        assert doc_id in ids
