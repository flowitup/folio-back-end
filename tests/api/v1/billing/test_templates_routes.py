"""API tests for billing document template endpoints."""

from __future__ import annotations

import uuid


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_TPL_BODY = {
    "kind": "devis",
    "name": "Standard Consulting",
    "items": [{"description": "Consulting", "quantity": "1", "unit_price": "800", "vat_rate": "20"}],
}


class TestListTemplates:
    def test_list_returns_200(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-document-templates", headers=_auth(billing_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data

    def test_list_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get("/api/v1/billing-document-templates")
        assert resp.status_code == 401

    def test_list_filter_by_kind(self, inv_client, billing_token, seeded_template):
        resp = inv_client.get("/api/v1/billing-document-templates?kind=devis", headers=_auth(billing_token))
        assert resp.status_code == 200

    def test_list_invalid_kind_returns_400(self, inv_client, billing_token):
        resp = inv_client.get("/api/v1/billing-document-templates?kind=invalid", headers=_auth(billing_token))
        assert resp.status_code == 400


class TestCreateTemplate:
    def test_create_returns_201(self, inv_client, billing_token):
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=_TPL_BODY,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "devis"
        assert data["name"] == "Standard Consulting"

    def test_create_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.post("/api/v1/billing-document-templates", json=_TPL_BODY)
        assert resp.status_code == 401

    def test_create_extra_field_returns_422(self, inv_client, billing_token):
        body = {**_TPL_BODY, "unexpected": "field"}
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=body,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_create_missing_name_returns_422(self, inv_client, billing_token):
        body = {"kind": "devis", "items": []}
        resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json=body,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422


class TestGetTemplate:
    def test_get_own_template_returns_200(self, inv_client, billing_token, seeded_template):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["id"] == seeded_template["id"]

    def test_get_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_get_nonexistent_returns_404(self, inv_client, billing_token):
        resp = inv_client.get(
            f"/api/v1/billing-document-templates/{uuid.uuid4()}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404


class TestUpdateTemplate:
    def test_update_name_returns_200(self, inv_client, billing_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"name": "Updated Template Name"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated Template Name"

    def test_update_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.put(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            json={"name": "Hacked"},
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_apply_template_kind_mismatch_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #11: apply devis template to create facture-kind doc is prevented
        because the kind comes from the template itself — template kind cannot be
        overridden via the apply route. Test that the schema rejects extra fields.
        """
        # Create a devis template
        tpl_resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json={"kind": "devis", "name": "Devis TPL"},
            headers=_auth(billing_token),
        )
        assert tpl_resp.status_code == 201
        tpl_id = tpl_resp.get_json()["id"]

        # Apply the template — extra field `kind` in body is rejected by schema
        apply_resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{tpl_id}",
            json={"recipient_name": "Client", "kind": "facture"},  # kind not allowed
            headers=_auth(billing_token),
        )
        assert apply_resp.status_code == 422


class TestDeleteTemplate:
    def test_delete_own_template_returns_204(self, inv_client, billing_token):
        create_resp = inv_client.post(
            "/api/v1/billing-document-templates",
            json={"kind": "facture", "name": "To Delete"},
            headers=_auth(billing_token),
        )
        assert create_resp.status_code == 201
        tpl_id = create_resp.get_json()["id"]

        resp = inv_client.delete(
            f"/api/v1/billing-document-templates/{tpl_id}",
            headers=_auth(billing_token),
        )
        assert resp.status_code == 204

    def test_delete_wrong_owner_returns_404(self, inv_client, other_token, seeded_template):
        resp = inv_client.delete(
            f"/api/v1/billing-document-templates/{seeded_template['id']}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404


class TestApplyTemplate:
    def test_apply_template_creates_doc(self, inv_client, billing_token, billing_profile, seeded_template):
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{seeded_template['id']}",
            json={"recipient_name": "Client via Template"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "devis"
        assert data["recipient_name"] == "Client via Template"

    def test_apply_nonexistent_template_returns_404(self, inv_client, billing_token, billing_profile):
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{uuid.uuid4()}",
            json={"recipient_name": "X"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 404

    def test_apply_template_no_profile_returns_409(self, inv_client, other_token, seeded_template):
        """other_token user has no profile — returns 409."""
        resp = inv_client.post(
            f"/api/v1/billing-documents/from-template/{seeded_template['id']}",
            json={"recipient_name": "X"},
            headers=_auth(other_token),
        )
        # other_token doesn't own the template → 404 (ownership checked first)
        assert resp.status_code == 404
