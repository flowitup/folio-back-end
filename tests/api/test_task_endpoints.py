"""Task endpoints: assignee validation and clearing fields with an explicit null."""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create(client, token, project_id, **body):
    return client.post(
        f"/api/v1/projects/{project_id}/tasks", json={"title": "Pour slab", **body}, headers=_auth(token)
    )


def test_assigning_a_user_outside_the_project_is_a_400(inv_client, admin_token, invitation_app):
    outsider = invitation_app._test_superadmin_user_id  # attached to no company
    resp = _create(inv_client, admin_token, invitation_app._test_project_id, assignee_id=outsider)
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_assigning_an_unknown_user_is_a_400_not_a_500(inv_client, admin_token, invitation_app):
    resp = _create(
        inv_client, admin_token, invitation_app._test_project_id, assignee_id="00000000-0000-4000-8000-000000000001"
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_assigning_a_member_of_the_project_is_accepted(inv_client, admin_token, invitation_app):
    member = invitation_app._test_member_user_id
    resp = _create(inv_client, admin_token, invitation_app._test_project_id, assignee_id=member)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["assignee_id"] == member


def test_put_with_null_clears_description_assignee_and_due_date(inv_client, admin_token, invitation_app):
    created = _create(
        inv_client,
        admin_token,
        invitation_app._test_project_id,
        description="Bring the mixer",
        assignee_id=invitation_app._test_member_user_id,
        due_date="2026-10-01",
    ).get_json()

    resp = inv_client.put(
        f"/api/v1/tasks/{created['id']}",
        json={"description": None, "assignee_id": None, "due_date": None},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert (body["description"], body["assignee_id"], body["due_date"]) == (None, None, None)

    untouched = inv_client.put(f"/api/v1/tasks/{created['id']}", json={"title": "Renamed"}, headers=_auth(admin_token))
    assert untouched.get_json()["title"] == "Renamed"
