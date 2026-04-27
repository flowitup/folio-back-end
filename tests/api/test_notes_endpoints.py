"""Integration tests for project-scoped notes endpoints (4 routes)."""

from __future__ import annotations

import uuid
from datetime import date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _notes_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/notes"


def _note_url(project_id: str, note_id: str) -> str:
    return f"/api/v1/projects/{project_id}/notes/{note_id}"


def _valid_body(**overrides) -> dict:
    base = {
        "title": "Test note",
        "description": None,
        "due_date": str(date.today()),
        "lead_time_minutes": 0,
    }
    base.update(overrides)
    return base


# ===========================================================================
# POST /api/v1/projects/<project_id>/notes  — create note
# ===========================================================================


class TestCreateNoteEndpoint:
    def test_201_member_creates_note(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="Brand new note"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Brand new note"
        assert data["status"] == "open"
        assert "id" in data
        assert "fire_at" in data
        assert "created_at" in data

    def test_201_response_shape_complete(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        required_keys = {
            "id",
            "project_id",
            "created_by",
            "title",
            "description",
            "due_date",
            "lead_time_minutes",
            "status",
            "fire_at",
            "created_at",
            "updated_at",
        }
        assert required_keys.issubset(data.keys())

    def test_201_with_lead_time_60(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(lead_time_minutes=60),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["lead_time_minutes"] == 60

    def test_201_with_lead_time_1440(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(lead_time_minutes=1440),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["lead_time_minutes"] == 1440

    def test_401_unauthenticated(self, inv_client, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(),
        )
        assert resp.status_code == 401

    def test_403_non_member_cannot_create(self, inv_client, non_member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(),
            headers=_auth(non_member_token),
        )
        assert resp.status_code == 403

    def test_400_invalid_lead_time(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(lead_time_minutes=30),
            headers=_auth(member_token),
        )
        # Pydantic Literal[0,60,1440] rejects 30 at schema layer → 422
        assert resp.status_code in (400, 422)

    def test_422_missing_title(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json={"due_date": str(date.today())},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_422_missing_due_date(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json={"title": "No date"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_422_title_too_long(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="A" * 201),
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_past_due_date_accepted(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(due_date="2020-01-01"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201


# ===========================================================================
# GET /api/v1/projects/<project_id>/notes  — list notes
# ===========================================================================


class TestListNotesEndpoint:
    def test_200_returns_items_and_count(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])

    def test_200_includes_open_note(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.get_json()["items"]]
        assert note_open in ids

    def test_200_includes_done_note(self, inv_client, member_token, invitation_app, note_done):
        resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.get_json()["items"]]
        assert note_done in ids

    def test_401_unauthenticated(self, inv_client, invitation_app):
        resp = inv_client.get(_notes_url(invitation_app._test_project_id))
        assert resp.status_code == 401

    def test_403_non_member_cannot_list(self, inv_client, non_member_token, invitation_app):
        resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(non_member_token),
        )
        assert resp.status_code == 403


# ===========================================================================
# PATCH /api/v1/projects/<project_id>/notes/<note_id>  — update note
# ===========================================================================


class TestUpdateNoteEndpoint:
    def test_200_update_title(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"title": "Updated title"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "Updated title"

    def test_200_mark_done_via_status_field(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "done"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "done"

    def test_200_reopen_via_status_field(self, inv_client, member_token, invitation_app, note_done):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_done),
            json={"status": "open"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "open"

    def test_200_update_due_date_clears_dismissals(
        self, inv_client, member_token, invitation_app, note_dismissed_by_member
    ):
        """Changing due_date cascades dismissals — endpoint returns 200."""
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_dismissed_by_member),
            json={"due_date": "2027-01-01"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200

    def test_401_unauthenticated(self, inv_client, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"title": "No auth"},
        )
        assert resp.status_code == 401

    def test_403_non_member_cannot_update(self, inv_client, non_member_token, invitation_app, note_other_project):
        note_id, project_id = note_other_project
        resp = inv_client.patch(
            _note_url(project_id, note_id),
            json={"title": "Forbidden"},
            headers=_auth(non_member_token),
        )
        assert resp.status_code == 403

    def test_404_nonexistent_note(self, inv_client, member_token, invitation_app):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, str(uuid.uuid4())),
            json={"title": "Ghost"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_422_invalid_lead_time_value(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"lead_time_minutes": 30},
            headers=_auth(member_token),
        )
        assert resp.status_code in (400, 422)

    def test_422_invalid_status_value(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "archived"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_patch_description_persists(self, inv_client, member_token, invitation_app):
        """C1 regression: PATCH with description must update and be reflected in response."""
        create_resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="Desc test note"),
            headers=_auth(member_token),
        )
        assert create_resp.status_code == 201
        note_id = create_resp.get_json()["id"]

        patch_resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_id),
            json={"description": "updated description"},
            headers=_auth(member_token),
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["description"] == "updated description"

        # Verify via list that the change is persisted
        list_resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        items = {item["id"]: item for item in list_resp.get_json()["items"]}
        assert items[note_id]["description"] == "updated description"


# ===========================================================================
# DELETE /api/v1/projects/<project_id>/notes/<note_id>  — delete note
# ===========================================================================


class TestDeleteNoteEndpoint:
    def test_204_member_deletes_own_note(self, inv_client, member_token, invitation_app):
        # Create a note specifically for deletion
        create_resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="To be deleted"),
            headers=_auth(member_token),
        )
        assert create_resp.status_code == 201
        note_id = create_resp.get_json()["id"]

        resp = inv_client.delete(
            _note_url(invitation_app._test_project_id, note_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 204
        assert resp.data == b""

    def test_204_deleted_note_no_longer_in_list(self, inv_client, member_token, invitation_app):
        create_resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="Delete me"),
            headers=_auth(member_token),
        )
        note_id = create_resp.get_json()["id"]

        inv_client.delete(
            _note_url(invitation_app._test_project_id, note_id),
            headers=_auth(member_token),
        )

        list_resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        ids = [item["id"] for item in list_resp.get_json()["items"]]
        assert note_id not in ids

    def test_401_unauthenticated(self, inv_client, invitation_app, note_open):
        resp = inv_client.delete(
            _note_url(invitation_app._test_project_id, note_open),
        )
        assert resp.status_code == 401

    def test_403_non_member_cannot_delete(self, inv_client, non_member_token, invitation_app, note_other_project):
        note_id, project_id = note_other_project
        resp = inv_client.delete(
            _note_url(project_id, note_id),
            headers=_auth(non_member_token),
        )
        assert resp.status_code == 403

    def test_404_nonexistent_note(self, inv_client, member_token, invitation_app):
        resp = inv_client.delete(
            _note_url(invitation_app._test_project_id, str(uuid.uuid4())),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404


# ===========================================================================
# 500 — unexpected use-case exception
# ===========================================================================


class TestNotesEndpointInternalError:
    def test_500_create_on_unexpected_exception(self, inv_client, member_token, invitation_app, monkeypatch):
        import wiring

        original = wiring.get_container().create_note_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Simulated crash")

        monkeypatch.setattr(wiring.get_container(), "create_note_usecase", _Broken())
        try:
            resp = inv_client.post(
                _notes_url(invitation_app._test_project_id),
                json=_valid_body(),
                headers=_auth(member_token),
            )
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "create_note_usecase", original)

    def test_500_list_on_unexpected_exception(self, inv_client, member_token, invitation_app, monkeypatch):
        import wiring

        original = wiring.get_container().list_project_notes_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Boom list")

        monkeypatch.setattr(wiring.get_container(), "list_project_notes_usecase", _Broken())
        try:
            resp = inv_client.get(
                _notes_url(invitation_app._test_project_id),
                headers=_auth(member_token),
            )
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "list_project_notes_usecase", original)

    def test_500_update_on_unexpected_exception(self, inv_client, member_token, invitation_app, note_open, monkeypatch):
        import wiring

        original = wiring.get_container().update_note_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Boom update")

        monkeypatch.setattr(wiring.get_container(), "update_note_usecase", _Broken())
        try:
            resp = inv_client.patch(
                _note_url(invitation_app._test_project_id, note_open),
                json={"title": "crash"},
                headers=_auth(member_token),
            )
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "update_note_usecase", original)

    def test_500_delete_on_unexpected_exception(self, inv_client, member_token, invitation_app, note_open, monkeypatch):
        import wiring

        original = wiring.get_container().delete_note_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Boom delete")

        monkeypatch.setattr(wiring.get_container(), "delete_note_usecase", _Broken())
        try:
            resp = inv_client.delete(
                _note_url(invitation_app._test_project_id, note_open),
                headers=_auth(member_token),
            )
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "delete_note_usecase", original)
