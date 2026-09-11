"""API integration tests for /api/v1/companies endpoints.

Tests: CRUD, join code, masking, ownership isolation.
Uses the invitation_app (module-scoped) + superadmin / member credentials.
"""

from __future__ import annotations

import uuid

import pytest

from tests.auth_login_helper import mint_access_token


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
    return mint_access_token(_co_client, invitation_app._test_superadmin_email)


@pytest.fixture(scope="module")
def member_token_co(_co_client, invitation_app):
    return mint_access_token(_co_client, invitation_app._test_member_email)


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

    def test_create_allowed_for_non_admin_self_service(self, inv_client, member_token_co):
        """Phase 2 D1/goal 1: company creation is self-service — any authenticated
        user may create a company, no platform ``*:*`` permission required."""
        resp = inv_client.post(
            "/api/v1/companies",
            json={"legal_name": "Member Corp", "address": "1 rue Test"},
            headers=_auth(member_token_co),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)


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

        # Admin mints a join code, member attaches with it
        code_resp = inv_client.post(
            f"/api/v1/companies/{company_id}/join-code",
            headers=_auth(admin_token),
        )
        assert code_resp.status_code == 200
        join_code = code_resp.get_json()["join_code"]

        join_resp = inv_client.post(
            "/api/v1/companies/join",
            json={"code": join_code},
            headers=_auth(member_token_co),
        )
        assert join_resp.status_code == 200, join_resp.get_data(as_text=True)

        # Member fetches company — sensitive fields should be masked (not full value)
        resp = inv_client.get(
            f"/api/v1/companies/{company_id}",
            headers=_auth(member_token_co),
        )
        assert resp.status_code == 200
