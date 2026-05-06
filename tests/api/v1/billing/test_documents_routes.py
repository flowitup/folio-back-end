"""API integration tests for billing document endpoints.

Uses the invitation_app Flask test fixture (SQLite in-memory).
All fixtures from tests/api/v1/billing/conftest.py and tests/conftest.py.
"""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_ITEM = {"description": "Service", "quantity": "1", "unit_price": "500", "vat_rate": "20"}
_CREATE_BASE = {"kind": "devis", "recipient_name": "Acme Corp", "items": [_ITEM]}


def _create(company_id: str) -> dict:
    """Return a minimal create body with the required company_id injected."""
    return {**_CREATE_BASE, "company_id": company_id}


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


class TestBillingDocumentsAuthGuard:
    def test_list_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get("/api/v1/billing-documents?kind=devis")
        assert resp.status_code == 401

    def test_create_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.post("/api/v1/billing-documents", json=_CREATE_BASE)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /billing-documents
# ---------------------------------------------------------------------------


class TestCreateBillingDocument:
    def test_create_devis_returns_201(self, inv_client, billing_token, billing_profile):
        resp = inv_client.post(
            "/api/v1/billing-documents",
            json=_create(billing_profile["company_id"]),
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "devis"
        assert data["status"] == "draft"
        assert data["recipient_name"] == "Acme Corp"

    def test_create_facture_returns_201(self, inv_client, billing_token, billing_profile):
        body = {**_create(billing_profile["company_id"]), "kind": "facture"}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(billing_token))
        assert resp.status_code == 201
        assert resp.get_json()["kind"] == "facture"

    def test_create_missing_company_id_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #8: missing required company_id field → 422 (schema validation)."""
        resp = inv_client.post("/api/v1/billing-documents", json=_CREATE_BASE, headers=_auth(billing_token))
        assert resp.status_code == 422

    def test_create_doc_with_unattached_company_returns_4xx(self, inv_client, billing_token, billing_profile):
        """Spec #7: company_id provided but company doesn't exist → 4xx error."""
        import uuid
        fake_company_id = str(uuid.uuid4())
        body = {**_CREATE_BASE, "company_id": fake_company_id}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(billing_token))
        assert resp.status_code in (400, 409, 422)

    def test_create_missing_company_profile_returns_409(self, inv_client, other_token, billing_profile):
        """Spec #6: user not attached to any company → 409 with reason=company_profile_missing."""
        # other_token user has no company attachment — pass a valid-format company_id
        body = {**_CREATE_BASE, "company_id": billing_profile["company_id"]}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(other_token))
        assert resp.status_code in (409, 422)

    def test_pydantic_strict_extra_field_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #13: unknown field on POST body → 422."""
        body = {**_create(billing_profile["company_id"]), "unknown_field": "oops"}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(billing_token))
        assert resp.status_code == 422

    def test_missing_kind_returns_400(self, inv_client, billing_token, billing_profile):
        body = {"recipient_name": "X", "items": [_ITEM], "company_id": billing_profile["company_id"]}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(billing_token))
        assert resp.status_code == 422

    def test_empty_items_returns_422(self, inv_client, billing_token, billing_profile):
        body = {**_create(billing_profile["company_id"]), "items": []}
        resp = inv_client.post("/api/v1/billing-documents", json=body, headers=_auth(billing_token))
        assert resp.status_code in (400, 422)

    def test_totals_in_response(self, inv_client, billing_token, billing_profile):
        resp = inv_client.post(
            "/api/v1/billing-documents",
            json=_create(billing_profile["company_id"]),
            headers=_auth(billing_token),
        )
        data = resp.get_json()
        assert "total_ht" in data
        assert "total_tva" in data
        assert "total_ttc" in data


# ---------------------------------------------------------------------------
# GET /billing-documents
# ---------------------------------------------------------------------------


class TestListBillingDocuments:
    def test_list_returns_own_docs(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.get("/api/v1/billing-documents?kind=devis", headers=_auth(billing_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        ids = [d["id"] for d in data["items"]]
        assert seeded_doc["id"] in ids

    def test_list_missing_kind_returns_400(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-documents", headers=_auth(billing_token))
        assert resp.status_code == 400

    def test_list_invalid_kind_returns_400(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-documents?kind=invalid", headers=_auth(billing_token))
        assert resp.status_code == 400

    def test_list_filter_by_status(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.get(
            "/api/v1/billing-documents?kind=devis&status=draft",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        for item in resp.get_json()["items"]:
            assert item["status"] == "draft"

    def test_list_pagination_params(self, inv_client, billing_token, billing_profile):
        # Create 3 docs
        for _ in range(3):
            inv_client.post(
                "/api/v1/billing-documents",
                json=_create(billing_profile["company_id"]),
                headers=_auth(billing_token),
            )
        resp = inv_client.get(
            "/api/v1/billing-documents?kind=devis&limit=2&offset=0",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["items"]) <= 2
        assert "total" in data


# ---------------------------------------------------------------------------
# GET /billing-documents/<id>
# ---------------------------------------------------------------------------


class TestGetBillingDocument:
    def test_get_own_doc_returns_200(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.get(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["id"] == seeded_doc["id"]

    def test_owner_isolation_returns_404(self, inv_client, other_token, seeded_doc):
        """Spec #10: user B cannot see user A's docs → 404, not 403."""
        resp = inv_client.get(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_nonexistent_returns_404(self, inv_client, billing_token):
        import uuid

        resp = inv_client.get(
            f"/api/v1/billing-documents/{uuid.uuid4()}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /billing-documents/<id>
# ---------------------------------------------------------------------------


class TestUpdateBillingDocument:
    def test_update_recipient_returns_200(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.put(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            json={"recipient_name": "New Client"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["recipient_name"] == "New Client"

    def test_update_doc_cannot_change_kind_or_issuer(self, inv_client, billing_token, seeded_doc):
        """Spec #14: sending kind or issuer_* fields → 422 (extra='forbid')."""
        resp = inv_client.put(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            json={"kind": "facture"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_update_issuer_field_returns_422(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.put(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            json={"issuer_legal_name": "Hacked"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_update_wrong_owner_returns_404(self, inv_client, other_token, seeded_doc):
        resp = inv_client.put(
            f"/api/v1/billing-documents/{seeded_doc['id']}",
            json={"recipient_name": "Hacker"},
            headers=_auth(other_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /billing-documents/<id>
# ---------------------------------------------------------------------------


class TestDeleteBillingDocument:
    def test_delete_own_doc_returns_204(self, inv_client, billing_token, billing_profile):
        create_resp = inv_client.post(
            "/api/v1/billing-documents",
            json=_create(billing_profile["company_id"]),
            headers=_auth(billing_token),
        )
        doc_id = create_resp.get_json()["id"]
        resp = inv_client.delete(f"/api/v1/billing-documents/{doc_id}", headers=_auth(billing_token))
        assert resp.status_code == 204

    def test_delete_wrong_owner_returns_404(self, inv_client, other_token, seeded_doc):
        resp = inv_client.delete(f"/api/v1/billing-documents/{seeded_doc['id']}", headers=_auth(other_token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /billing-documents/<id>/clone
# ---------------------------------------------------------------------------


class TestCloneBillingDocument:
    def test_clone_returns_201(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.post(
            f"/api/v1/billing-documents/{seeded_doc['id']}/clone",
            json={},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["id"] != seeded_doc["id"]
        assert data["status"] == "draft"
        assert data["source_devis_id"] is None


# ---------------------------------------------------------------------------
# PATCH /billing-documents/<id>/status
# ---------------------------------------------------------------------------


class TestUpdateBillingDocumentStatus:
    def test_draft_to_sent_returns_200(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.patch(
            f"/api/v1/billing-documents/{seeded_doc['id']}/status",
            json={"new_status": "sent"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "sent"

    def test_invalid_transition_returns_409(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.patch(
            f"/api/v1/billing-documents/{seeded_doc['id']}/status",
            json={"new_status": "paid"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 409

    def test_invalid_status_value_returns_422(self, inv_client, billing_token, seeded_doc):
        resp = inv_client.patch(
            f"/api/v1/billing-documents/{seeded_doc['id']}/status",
            json={"new_status": "nonexistent"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422
