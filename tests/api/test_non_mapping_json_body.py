"""A JSON body that is not an object (array, string, number) is a validation error, not a 500."""

from __future__ import annotations

import json

import pytest

from tests.auth_login_helper import mint_tokens

NON_MAPPING_BODIES = [["x"], "str", 123]

PUBLIC_ENDPOINTS = [
    ("POST", "/api/v1/auth/otp/request"),
    ("POST", "/api/v1/auth/otp/verify"),
    ("POST", "/api/v1/auth/signup/request"),
    ("POST", "/api/v1/auth/signup/verify"),
    ("POST", "/api/v1/invitations/accept"),
]


def _authed_endpoints(app) -> list[tuple[str, str]]:
    company_id = app._test_company_id
    project_id = app._test_project_id
    member_id = app._test_member_user_id
    return [
        ("PATCH", "/api/v1/auth/me"),
        ("POST", "/api/v1/companies/join"),
        ("PATCH", f"/api/v1/companies/{company_id}/access/{member_id}/role"),
        ("POST", "/api/v1/projects"),
        ("PUT", f"/api/v1/projects/{project_id}"),
        ("POST", "/api/v1/labor/roles"),
        ("POST", "/api/v1/persons"),
        ("POST", "/api/v1/push/devices"),
        ("POST", "/api/v1/invitations"),
        ("POST", f"/api/v1/projects/{project_id}/tasks"),
    ]


def _send(client, method: str, url: str, body, headers=None):
    return client.open(url, method=method, data=json.dumps(body), content_type="application/json", headers=headers)


@pytest.mark.parametrize("body", NON_MAPPING_BODIES, ids=["list", "string", "number"])
@pytest.mark.parametrize("method,url", PUBLIC_ENDPOINTS)
def test_public_endpoints_reject_a_non_object_body(invitation_app, method, url, body):
    resp = _send(invitation_app.test_client(), method, url, body)
    assert resp.status_code in (400, 422), resp.data
    assert resp.is_json


@pytest.mark.parametrize("body", NON_MAPPING_BODIES, ids=["list", "string", "number"])
def test_signed_in_endpoints_reject_a_non_object_body(invitation_app, body):
    client = invitation_app.test_client()
    token = mint_tokens(client, invitation_app._test_admin_email)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    for method, url in _authed_endpoints(invitation_app):
        resp = _send(client, method, url, body, headers)
        assert resp.status_code in (400, 422), (method, url, resp.status_code, resp.data)
        assert resp.is_json, (method, url)
