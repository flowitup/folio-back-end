"""POST, PUT and the list answer a project the same way GET does."""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_create_and_update_answer_company_and_permissions_like_get(inv_client, admin_token, invitation_app):
    created = inv_client.post(
        "/api/v1/projects", json={"name": "Consistency P", "address": "1 rue X"}, headers=_auth(admin_token)
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    body = created.get_json()
    assert body["company_id"] == invitation_app._test_company_id
    assert "project:update" in body["my_permissions"]

    pid = body["id"]
    updated = inv_client.put(f"/api/v1/projects/{pid}", json={"name": "Consistency P2"}, headers=_auth(admin_token))
    assert updated.status_code == 200
    got = inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(admin_token)).get_json()
    assert updated.get_json()["company_id"] == got["company_id"] == invitation_app._test_company_id
    assert updated.get_json()["my_permissions"] == got["my_permissions"]
    assert body["my_permissions"] == got["my_permissions"]


def test_list_carries_each_project_creation_date(inv_client, admin_token):
    projects = inv_client.get("/api/v1/projects", headers=_auth(admin_token)).get_json()["projects"]
    assert projects
    assert all(p["created_at"] for p in projects)
