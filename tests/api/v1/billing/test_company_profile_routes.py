"""API tests for company profile endpoints."""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_PROFILE_BODY = {
    "legal_name": "Acme SAS",
    "address": "1 rue de la Paix, 75001 Paris",
}


class TestGetCompanyProfile:
    def test_get_returns_404_when_not_set(self, inv_client, other_token):
        """other_token user has no profile — returns 404."""
        resp = inv_client.get("/api/v1/company-profile", headers=_auth(other_token))
        assert resp.status_code == 404

    def test_get_returns_200_when_set(self, inv_client, billing_token, billing_profile):
        resp = inv_client.get("/api/v1/company-profile", headers=_auth(billing_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["legal_name"] == billing_profile["legal_name"]

    def test_get_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get("/api/v1/company-profile")
        assert resp.status_code == 401


class TestUpsertCompanyProfile:
    def test_create_profile_returns_200(self, inv_client, billing_token):
        resp = inv_client.put(
            "/api/v1/company-profile",
            json=_PROFILE_BODY,
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["legal_name"] == "Acme SAS"
        assert data["address"] == "1 rue de la Paix, 75001 Paris"

    def test_update_profile_returns_200(self, inv_client, billing_token, billing_profile):
        resp = inv_client.put(
            "/api/v1/company-profile",
            json={**_PROFILE_BODY, "legal_name": "Updated Company SAS"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["legal_name"] == "Updated Company SAS"

    def test_upsert_with_optional_fields(self, inv_client, billing_token):
        resp = inv_client.put(
            "/api/v1/company-profile",
            json={
                **_PROFILE_BODY,
                "siret": "12345678901234",
                "tva_number": "FR12345678901",
                "iban": "FR76123456789012345678901",
                "bic": "BNPAFRPPXXX",
                "prefix_override": "FLW",
                "default_payment_terms": "30 jours net",
            },
            headers=_auth(billing_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["siret"] == "12345678901234"
        assert data["prefix_override"] == "FLW"

    def test_upsert_missing_legal_name_returns_422(self, inv_client, billing_token):
        resp = inv_client.put(
            "/api/v1/company-profile",
            json={"address": "1 rue de la Paix"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_upsert_extra_field_returns_422(self, inv_client, billing_token):
        resp = inv_client.put(
            "/api/v1/company-profile",
            json={**_PROFILE_BODY, "unknown_field": "nope"},
            headers=_auth(billing_token),
        )
        assert resp.status_code == 422

    def test_upsert_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.put("/api/v1/company-profile", json=_PROFILE_BODY)
        assert resp.status_code == 401

    def test_user_isolation(self, inv_client, billing_token, other_token, billing_profile):
        """Each user has their own profile — other_token user still has none."""
        resp = inv_client.get("/api/v1/company-profile", headers=_auth(other_token))
        assert resp.status_code == 404
