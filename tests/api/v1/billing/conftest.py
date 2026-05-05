"""Shared fixtures for billing API tests.

Re-uses the invitation_app fixture (which wires the full Flask test app)
and adds billing-specific fixtures: tokens, seeded company profile, seeded docs.
"""

from __future__ import annotations

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tokens — re-use existing invitation_app users
# ---------------------------------------------------------------------------


@pytest.fixture
def billing_token(inv_client, invitation_app):
    """JWT token for the admin user (owns billing data in tests)."""
    resp = inv_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_admin_email,
            "password": invitation_app._test_admin_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


@pytest.fixture
def other_token(inv_client, invitation_app):
    """JWT token for a different user (used for ownership isolation tests)."""
    resp = inv_client.post(
        "/api/v1/auth/login",
        json={
            "email": invitation_app._test_member_email,
            "password": invitation_app._test_member_password,
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["access_token"]


# ---------------------------------------------------------------------------
# Company profile seed
# ---------------------------------------------------------------------------


@pytest.fixture
def billing_profile(inv_client, billing_token):
    """Upsert a company profile for the billing_token user, return it."""
    resp = inv_client.put(
        "/api/v1/company-profile",
        json={
            "legal_name": "Test Company SAS",
            "address": "1 rue de la Paix, 75001 Paris",
            "siret": "12345678901234",
            "prefix_override": "TST",
        },
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# ---------------------------------------------------------------------------
# Seeded billing document
# ---------------------------------------------------------------------------

_MINIMAL_CREATE_BODY = {
    "kind": "devis",
    "recipient_name": "Acme Corp",
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
        json=_MINIMAL_CREATE_BODY,
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


@pytest.fixture
def seeded_accepted_devis(inv_client, billing_token, billing_profile):
    """Create a devis, transition it to sent then accepted, return it."""
    # Create
    resp = inv_client.post(
        "/api/v1/billing-documents",
        json=_MINIMAL_CREATE_BODY,
        headers=_auth(billing_token),
    )
    assert resp.status_code == 201
    doc = resp.get_json()
    doc_id = doc["id"]

    # draft → sent
    resp = inv_client.patch(
        f"/api/v1/billing-documents/{doc_id}/status",
        json={"new_status": "sent"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200

    # sent → accepted
    resp = inv_client.patch(
        f"/api/v1/billing-documents/{doc_id}/status",
        json={"new_status": "accepted"},
        headers=_auth(billing_token),
    )
    assert resp.status_code == 200
    return resp.get_json()


@pytest.fixture
def seeded_template(inv_client, billing_token):
    """Create and return a billing template owned by billing_token user.

    Uses a UUID suffix to avoid the unique(user_id, kind, name) constraint
    when multiple tests in the same module-scoped DB request this fixture.
    """
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
