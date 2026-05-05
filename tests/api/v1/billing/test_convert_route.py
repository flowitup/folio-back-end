"""API tests for POST /billing-documents/<id>/convert-to-facture."""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestConvertDevisToFacture:
    def test_convert_accepted_devis_returns_201(self, inv_client, billing_token, seeded_accepted_devis):
        """Happy path: accepted devis → facture draft."""
        resp = inv_client.post(
            f"/api/v1/billing-documents/{seeded_accepted_devis['id']}/convert-to-facture",
            json={},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["kind"] == "facture"
        assert data["status"] == "draft"
        assert data["source_devis_id"] == seeded_accepted_devis["id"]

    def test_convert_devis_already_converted_returns_409(self, inv_client, billing_token, seeded_accepted_devis):
        """Spec #7: second convert on the same accepted devis → 409."""
        doc_id = seeded_accepted_devis["id"]
        # First conversion
        r1 = inv_client.post(
            f"/api/v1/billing-documents/{doc_id}/convert-to-facture",
            json={},
            headers=_auth(billing_token),
        )
        assert r1.status_code == 201

        # Second conversion must fail
        r2 = inv_client.post(
            f"/api/v1/billing-documents/{doc_id}/convert-to-facture",
            json={},
            headers=_auth(billing_token),
        )
        assert r2.status_code == 409

    def test_convert_devis_not_accepted_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #9: devis with status=draft → 400 (ValueError mapped to 400)."""
        # Create a draft devis (never transitioned)
        create_resp = inv_client.post(
            "/api/v1/billing-documents",
            json={
                "kind": "devis",
                "recipient_name": "Draft Client",
                "items": [{"description": "X", "quantity": "1", "unit_price": "100", "vat_rate": "20"}],
            },
            headers=_auth(billing_token),
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.get_json()["id"]

        resp = inv_client.post(
            f"/api/v1/billing-documents/{doc_id}/convert-to-facture",
            json={},
            headers=_auth(billing_token),
        )
        # Route maps ValueError → 400
        assert resp.status_code == 400

    def test_convert_facture_source_returns_422(self, inv_client, billing_token, billing_profile):
        """Spec #8: facture as source → 400 (ValueError mapped to 400)."""
        # Create a facture
        create_resp = inv_client.post(
            "/api/v1/billing-documents",
            json={
                "kind": "facture",
                "recipient_name": "Facture Client",
                "items": [{"description": "X", "quantity": "1", "unit_price": "100", "vat_rate": "20"}],
            },
            headers=_auth(billing_token),
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.get_json()["id"]

        resp = inv_client.post(
            f"/api/v1/billing-documents/{doc_id}/convert-to-facture",
            json={},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 400

    def test_convert_wrong_owner_returns_404(self, inv_client, other_token, seeded_accepted_devis):
        resp = inv_client.post(
            f"/api/v1/billing-documents/{seeded_accepted_devis['id']}/convert-to-facture",
            json={},
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    def test_convert_unauthenticated_returns_401(self, inv_client, seeded_accepted_devis):
        resp = inv_client.post(
            f"/api/v1/billing-documents/{seeded_accepted_devis['id']}/convert-to-facture",
            json={},
        )
        assert resp.status_code == 401
