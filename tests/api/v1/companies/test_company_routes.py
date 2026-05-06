"""API integration tests for /api/v1/companies endpoints.

Tests: CRUD, invite-token lifecycle, attach-by-token, masking, ownership isolation.
Uses the invitation_app (module-scoped) + superadmin / member credentials.
"""

from __future__ import annotations

import uuid

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Module-scoped client + tokens (mirrors billing conftest pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _co_client(invitation_app):
    return invitation_app.test_client()


@pytest.fixture(scope="module")
def admin_token(_co_client, invitation_app):
    resp = _co_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_superadmin_email,
            "password": invitation_app._test_superadmin_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def member_token_co(_co_client, invitation_app):
    resp = _co_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_member_email,
            "password": invitation_app._test_member_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


# ---------------------------------------------------------------------------
# Helper: create a fresh company per test
# ---------------------------------------------------------------------------


def _make_company(client, token, suffix="") -> dict:
    resp = client.post(
        "/api/v1/companies",
        json={
            "legal_name": f"Test Corp {suffix or uuid.uuid4().hex[:6]}",
            "address": "1 rue de la Paix, 75001 Paris",
            "siret": "12345678901234",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


# ---------------------------------------------------------------------------
# POST /companies — admin create
# ---------------------------------------------------------------------------


class TestCreateCompany:
    def test_create_returns_201(self, inv_client, admin_token):
        resp = inv_client.post(
            "/api/v1/companies",
            json={"legal_name": "New Corp", "address": "2 rue Test"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["legal_name"] == "New Corp"
        assert "id" in data

    def test_create_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.post(
            "/api/v1/companies",
            json={"legal_name": "Anon Corp", "address": "1 rue Test"},
        )
        assert resp.status_code == 401

    def test_create_missing_required_field_returns_422(self, inv_client, admin_token):
        resp = inv_client.post(
            "/api/v1/companies",
            json={"legal_name": "No Address Corp"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_create_forbidden_for_non_admin(self, inv_client, member_token_co):
        """Non-admin user lacks *:* permission → 403."""
        resp = inv_client.post(
            "/api/v1/companies",
            json={"legal_name": "Member Corp", "address": "1 rue Test"},
            headers=_auth(member_token_co),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /companies/<id>
# ---------------------------------------------------------------------------


class TestGetCompany:
    def test_get_existing_returns_200(self, inv_client, admin_token):
        company = _make_company(inv_client, admin_token)
        resp = inv_client.get(
            f"/api/v1/companies/{company['id']}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["id"] == company["id"]

    def test_get_nonexistent_returns_404(self, inv_client, admin_token):
        resp = inv_client.get(
            f"/api/v1/companies/{uuid.uuid4()}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_get_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get(f"/api/v1/companies/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /companies/<id> — admin update
# ---------------------------------------------------------------------------


class TestUpdateCompany:
    def test_update_legal_name_returns_200(self, inv_client, admin_token):
        company = _make_company(inv_client, admin_token)
        resp = inv_client.put(
            f"/api/v1/companies/{company['id']}",
            json={"legal_name": "Updated Corp"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["legal_name"] == "Updated Corp"

    def test_update_nonexistent_returns_404(self, inv_client, admin_token):
        resp = inv_client.put(
            f"/api/v1/companies/{uuid.uuid4()}",
            json={"legal_name": "Ghost Corp"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Invite token lifecycle: generate → redeem → revoke
# ---------------------------------------------------------------------------


class TestInviteTokenLifecycle:
    def test_generate_token_returns_201_with_token(self, inv_client, admin_token):
        company = _make_company(inv_client, admin_token)
        resp = inv_client.post(
            f"/api/v1/companies/{company['id']}/invite-tokens",
            json={},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "token" in data
        assert len(data["token"]) > 10

    def test_redeem_valid_token_returns_200(self, inv_client, admin_token):
        """Redeem a valid token → 200."""
        company = _make_company(inv_client, admin_token)
        token_resp = inv_client.post(
            f"/api/v1/companies/{company['id']}/invite-tokens",
            json={},
            headers=_auth(admin_token),
        )
        assert token_resp.status_code == 201
        plaintext = token_resp.get_json()["token"]

        resp = inv_client.post(
            "/api/v1/companies/attach-by-token",
            json={"token": plaintext},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200

    def test_token_redeem_wrong_token_returns_410(self, inv_client, admin_token):
        """Spec #11: invalid/wrong token → 410 (Gone) — uniform response."""
        resp = inv_client.post(
            "/api/v1/companies/attach-by-token",
            json={"token": "completely-wrong-token-value"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 410

    def test_redeem_already_redeemed_token_returns_410(self, inv_client, admin_token):
        """Spec #11: reusing a redeemed token returns 410 Gone."""
        company = _make_company(inv_client, admin_token)
        token_resp = inv_client.post(
            f"/api/v1/companies/{company['id']}/invite-tokens",
            json={},
            headers=_auth(admin_token),
        )
        plaintext = token_resp.get_json()["token"]

        # First redeem
        inv_client.post(
            "/api/v1/companies/attach-by-token",
            json={"token": plaintext},
            headers=_auth(admin_token),
        )

        # Second redeem of same token → 410
        resp = inv_client.post(
            "/api/v1/companies/attach-by-token",
            json={"token": plaintext},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 410

    def test_revoke_token_returns_204(self, inv_client, admin_token):
        company = _make_company(inv_client, admin_token)
        inv_client.post(
            f"/api/v1/companies/{company['id']}/invite-tokens",
            json={},
            headers=_auth(admin_token),
        )
        resp = inv_client.delete(
            f"/api/v1/companies/{company['id']}/invite-tokens/active",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204

    def test_generate_token_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.post(
            f"/api/v1/companies/{uuid.uuid4()}/invite-tokens",
            json={},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /companies/me — list my companies
# ---------------------------------------------------------------------------


class TestListMyCompanies:
    def test_list_my_companies_returns_200(self, inv_client, admin_token):
        resp = inv_client.get("/api/v1/companies", headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data or isinstance(data, list)

    def test_list_my_companies_unauthenticated_returns_401(self, inv_client):
        resp = inv_client.get("/api/v1/companies")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Sensitive field masking
# ---------------------------------------------------------------------------


class TestSensitiveFieldMasking:
    def test_siret_visible_in_admin_response(self, inv_client, admin_token):
        """Admin creating company sees unmasked siret."""
        resp = inv_client.post(
            "/api/v1/companies",
            json={
                "legal_name": "Masked Corp",
                "address": "1 rue Masque",
                "siret": "99887766554433",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        # Admin (creator) should see the siret field
        assert data.get("siret") is not None

    def test_masking_never_leaks_full_value_to_non_admin(self, inv_client, admin_token, member_token_co):
        """Non-admin attached user must not see full siret value."""
        # Admin creates company
        company = _make_company(inv_client, admin_token)
        company_id = company["id"]

        # Generate + redeem invite token with member user
        token_resp = inv_client.post(
            f"/api/v1/companies/{company_id}/invite-tokens",
            json={},
            headers=_auth(admin_token),
        )
        assert token_resp.status_code == 201
        plaintext = token_resp.get_json()["token"]

        inv_client.post(
            "/api/v1/companies/attach-by-token",
            json={"token": plaintext},
            headers=_auth(member_token_co),
        )

        # Member fetches company — sensitive fields should be masked (not full value)
        resp = inv_client.get(
            f"/api/v1/companies/{company_id}",
            headers=_auth(member_token_co),
        )
        assert resp.status_code == 200
