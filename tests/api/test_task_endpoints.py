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
    # Nobody by that id: not "deactivated", which would describe an account that exists.
    assert resp.get_json()["message"] == "Assignee must be a member of this project"


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


def test_an_invalid_field_is_reported_by_name_without_pydantic_internals(inv_client, admin_token, invitation_app):
    created = _create(inv_client, admin_token, invitation_app._test_project_id).get_json()
    resp = inv_client.put(f"/api/v1/tasks/{created['id']}", json={"title": ""}, headers=_auth(admin_token))
    assert resp.status_code == 400
    message = resp.get_json()["message"]
    assert message.startswith("title: ")
    assert "pydantic.dev" not in message and "UpdateTaskSchema" not in message


def test_only_a_column_change_sends_the_moved_push(inv_client, admin_token, invitation_app, monkeypatch):
    from wiring import get_container

    moved = []

    class Recorder:
        def task_assigned(self, *, task, actor_id):
            pass

        def task_moved(self, *, task, actor_id):
            moved.append(task.status.value)

    with invitation_app.app_context():
        monkeypatch.setattr(get_container(), "task_push_notifier", Recorder())
    created = _create(
        inv_client, admin_token, invitation_app._test_project_id, assignee_id=invitation_app._test_member_user_id
    ).get_json()
    url = f"/api/v1/tasks/{created['id']}/move"

    reorder = inv_client.patch(url, json={"status": created["status"]}, headers=_auth(admin_token))
    assert reorder.status_code == 200, reorder.get_data(as_text=True)
    assert moved == []

    other = "done" if created["status"] != "done" else "todo"
    change = inv_client.patch(url, json={"status": other}, headers=_auth(admin_token))
    assert change.status_code == 200, change.get_data(as_text=True)
    assert moved == [other]


def test_labels_and_description_are_bounded(inv_client, admin_token, invitation_app):
    pid = invitation_app._test_project_id
    assert _create(inv_client, admin_token, pid, labels=["x" * 51]).status_code in (400, 422)
    assert _create(inv_client, admin_token, pid, labels=[f"l{i}" for i in range(21)]).status_code in (400, 422)
    assert _create(inv_client, admin_token, pid, description="d" * 5001).status_code in (400, 422)

    created = _create(inv_client, admin_token, pid, labels=["  urgent  ", "x" * 50], description="d" * 5000)
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["labels"] == ["urgent", "x" * 50]

    too_long = inv_client.put(
        f"/api/v1/tasks/{created.get_json()['id']}", json={"labels": ["y" * 51]}, headers=_auth(admin_token)
    )
    assert too_long.status_code in (400, 422)


def test_repeated_labels_are_kept_once_ignoring_case(inv_client, admin_token, invitation_app):
    pid = invitation_app._test_project_id
    created = _create(inv_client, admin_token, pid, labels=["Électricité", "Électricité", " électricité ", "a", "A"])
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["labels"] == ["Électricité", "a"]

    updated = inv_client.put(
        f"/api/v1/tasks/{created.get_json()['id']}", json={"labels": ["dup", "Dup", "b"]}, headers=_auth(admin_token)
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["labels"] == ["dup", "b"]


def test_a_malformed_project_or_task_id_is_a_400_not_a_missing_permission(inv_client, admin_token):
    """An id that is not a UUID names no row: telling an admin they lack project:read is wrong."""
    for url in ("/api/v1/projects/not-a-uuid/tasks", "/api/v1/tasks/not-a-uuid"):
        resp = inv_client.get(url, headers=_auth(admin_token))
        assert resp.status_code == 400, (url, resp.get_data(as_text=True))
