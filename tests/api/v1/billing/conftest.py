"""Shared fixtures for billing API tests.

Re-uses the invitation_app fixture (which wires the full Flask test app)
and adds billing-specific fixtures: tokens, seeded company, seeded docs.

Phase 05: billing_profile now creates a Company + UserCompanyAccess (primary)
for the billing_token user so company_id can be passed on every doc create call.
The old company-profile endpoint is still seeded for backward compat with tests
that exercise the legacy profile endpoint directly.

Scope note: billing_profile is MODULE-scoped to match invitation_app (module-scoped).
This prevents multiple companies being created per test-module — which would cause
document-number uniqueness collisions because each company resets its counter to 1.
"""

from __future__ import annotations

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Module-scoped client + tokens
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _billing_client(invitation_app):
    """Module-scoped test client shared by all billing fixtures."""
    return invitation_app.test_client()


@pytest.fixture(scope="module")
def billing_token(_billing_client, invitation_app):
    """JWT token for the admin user (superadmin — owns billing data in tests)."""
    resp = _billing_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_superadmin_email,
            "password": invitation_app._test_superadmin_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def other_token(_billing_client, invitation_app):
    """JWT token for member user (used for ownership isolation tests)."""
    resp = _billing_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_member_email,
            "password": invitation_app._test_member_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


# ---------------------------------------------------------------------------
# Company seed — module-scoped so only one company is created per test module.
# Multiple per-module companies would collide on document_number uniqueness
# because each company's counter restarts from 1 but the DB constraint is
# (user_id, kind, document_number) — not (company_id, kind, document_number).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def billing_profile(_billing_client, billing_token, invitation_app):
    """Create a Company (admin) + attach the billing_token user as primary.

    Module-scoped: runs once per test module, not once per test function.
    Returns a dict with at least: company_id, legal_name, address.
    Also seeds the legacy company-profile endpoint for tests that use it directly.
    """
    # 1. Create company via admin endpoint
    resp = _billing_client.post(
        "/api/v1/companies",
        json={
            "legal_name": "Test Company SAS",
            "address": "1 rue de la Paix, 75001 Paris",
            "siret": "12345678901234",
            "prefix_override": "TST",
        },
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, f"Create company failed: {resp.get_data(as_text=True)}"
    company = resp.get_json()
    company_id = company["id"]

    # 2. Generate invite token for the company
    resp = _billing_client.post(
        f"/api/v1/companies/{company_id}/invite-tokens",
        json={},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, f"Generate token failed: {resp.get_data(as_text=True)}"
    plaintext_token = resp.get_json()["token"]

    # 3. Redeem token to attach billing_token user to company (primary)
    resp = _billing_client.post(
        "/api/v1/companies/attach-by-token",
        json={"token": plaintext_token},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200, f"Redeem token failed: {resp.get_data(as_text=True)}"

    # 4. Also seed legacy company-profile for tests using that endpoint directly
    _billing_client.put(
        "/api/v1/company-profile",
        json={
            "legal_name": "Test Company SAS",
            "address": "1 rue de la Paix, 75001 Paris",
            "siret": "12345678901234",
            "prefix_override": "TST",
        },
        headers=_auth(billing_token),
    )

    return {**company, "company_id": company_id}


# ---------------------------------------------------------------------------
# Seeded billing document — now includes company_id in body
# Function-scoped: uses inv_client (function-scoped) so each test gets a
# fresh document while re-using the module-scoped billing_profile company.
# ---------------------------------------------------------------------------


def _minimal_create_body(company_id: str) -> dict:
    return {
        "kind": "devis",
        "recipient_name": "Acme Corp",
        "company_id": company_id,
        "items": [
            {
                "description": "Consulting",
                "quantity": "1",
                "unit_price": "1000",
                "vat_rate": "20",
            }
        ],
    }


@pytest.fixture
def seeded_doc(inv_client, billing_token, billing_profile):
    """Create and return a billing document owned by billing_token user."""
    resp = inv_client.post(
        "/api/v1/billing-documents",
        json=_minimal_create_body(billing_profile["company_id"]),
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


@pytest.fixture
def seeded_accepted_devis(inv_client, billing_token, billing_profile):
    """Create a devis, transition it to sent then accepted, return it."""
    resp = inv_client.post(
        "/api/v1/billing-documents",
        json=_minimal_create_body(billing_profile["company_id"]),
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201
    doc = resp.get_json()
    doc_id = doc["id"]

    resp = inv_client.patch(
        f"/api/v1/billing-documents/{doc_id}/status",
        json={"new_status": "sent"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200

    resp = inv_client.patch(
        f"/api/v1/billing-documents/{doc_id}/status",
        json={"new_status": "accepted"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200
    return resp.get_json()


@pytest.fixture
def seeded_template(inv_client, billing_token):
    """Create and return a billing template owned by billing_token user."""
    import uuid

    resp = inv_client.post(
        "/api/v1/billing-document-templates",
        json={
            "kind": "devis",
            "name": f"Standard Consulting {uuid.uuid4().hex[:8]}",
            "items": [
                {
                    "description": "Consulting",
                    "quantity": "1",
                    "unit_price": "800",
                    "vat_rate": "20",
                }
            ],
        },
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()
