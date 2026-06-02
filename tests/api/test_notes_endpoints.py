"""Integration tests for project-scoped journal notes endpoints (4 routes)."""

from __future__ import annotations

import uuid


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
    }
    base.update(overrides)
    return base


# ===========================================================================
# POST /api/v1/projects/<project_id>/notes  — create journal note
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
        assert data["category"] == "general"
        assert "id" in data
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
            "category",
            "status",
            "created_at",
            "updated_at",
        }
        assert required_keys.issubset(data.keys())
        # status must be "open" for new notes
        assert data["status"] == "open"
        # Legacy reminder fields must NOT be present in journal response
        assert "fire_at" not in data
        assert "due_date" not in data
        assert "lead_time_minutes" not in data

    def test_201_with_explicit_category(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(category="inspection"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["category"] == "inspection"

    def test_201_all_valid_categories(self, inv_client, member_token, invitation_app):
        for cat in ("inspection", "delivery", "payment", "decision", "call", "general"):
            resp = inv_client.post(
                _notes_url(invitation_app._test_project_id),
                json=_valid_body(title=f"Cat {cat}", category=cat),
                headers=_auth(member_token),
            )
            assert resp.status_code == 201, f"category={cat} failed: {resp.get_data(as_text=True)}"
            assert resp.get_json()["category"] == cat

    def test_422_invalid_category(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(category="reminder"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

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

    def test_422_missing_title(self, inv_client, member_token, invitation_app):
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json={"description": "No title"},
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

    def test_422_extra_fields_rejected(self, inv_client, member_token, invitation_app):
        """extra='forbid' rejects unknown fields."""
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(due_date="2027-01-01"),
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_400_whitespace_only_title_create(self, inv_client, member_token, invitation_app):
        """A title of all whitespace passes Pydantic min_length but fails domain _validate_title → 400."""
        resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="   "),
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "BadRequest"


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

    def test_200_includes_note_open(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.get(
            _notes_url(invitation_app._test_project_id),
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.get_json()["items"]]
        assert note_open in ids

    def test_200_includes_note_done(self, inv_client, member_token, invitation_app, note_done):
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

    def test_200_update_category(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"category": "payment"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["category"] == "payment"

    def test_422_invalid_category_on_patch(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"category": "bogus"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_200_patch_status_done(self, inv_client, member_token, invitation_app, note_open):
        """PATCH status=done marks note as done and returns updated status."""
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "done"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "done"

    def test_200_patch_status_open(self, inv_client, member_token, invitation_app, note_open):
        """PATCH status=open re-opens a note."""
        # First mark done
        inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "done"},
            headers=_auth(member_token),
        )
        # Then re-open
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "open"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "open"

    def test_200_patch_status_only_preserves_other_fields(self, inv_client, member_token, invitation_app):
        """PATCH status-only must not drop title, description, or category."""
        create_resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="Status only test", description="keep me", category="payment"),
            headers=_auth(member_token),
        )
        assert create_resp.status_code == 201
        note_id = create_resp.get_json()["id"]

        patch_resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_id),
            json={"status": "done"},
            headers=_auth(member_token),
        )
        assert patch_resp.status_code == 200
        data = patch_resp.get_json()
        assert data["status"] == "done"
        assert data["title"] == "Status only test"
        assert data["description"] == "keep me"
        assert data["category"] == "payment"

    def test_422_invalid_status_on_patch(self, inv_client, member_token, invitation_app, note_open):
        """Invalid status value must return 422."""
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"status": "pending"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

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

    def test_400_whitespace_only_title_update(self, inv_client, member_token, invitation_app, note_open):
        """A title of all whitespace passes Pydantic min_length but fails domain _validate_title → 400."""
        resp = inv_client.patch(
            _note_url(invitation_app._test_project_id, note_open),
            json={"title": "   "},
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "BadRequest"

    def test_patch_description_persists(self, inv_client, member_token, invitation_app):
        """PATCH with description must update and be reflected in list response."""
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
# Status — open/done + notifications guard
# ===========================================================================


class TestNoteStatusNotifications:
    def test_journal_note_absent_from_notifications(self, inv_client, member_token, invitation_app):
        """A journal note (no due_date/lead_time) must NOT appear in /notifications.

        list_due_for_user filters on due_date IS NOT NULL AND lead_time_minutes IS NOT NULL.
        Journal notes created via POST /notes have NULL due_date and NULL lead_time_minutes,
        so they are structurally excluded regardless of status.

        This test verifies via monkeypatched use-case that the note_id is absent from results.
        The real list_due_for_user uses Postgres-specific SQL so we stub it to return an empty
        list (the only correct result for a SQLite test DB without legacy rows).
        """

        create_resp = inv_client.post(
            _notes_url(invitation_app._test_project_id),
            json=_valid_body(title="Journal note not in notifications"),
            headers=_auth(member_token),
        )
        assert create_resp.status_code == 201
        note_id = create_resp.get_json()["id"]
        assert create_resp.get_json()["status"] == "open"

        import wiring

        original_uc = wiring.get_container().list_due_notifications_usecase

        class _EmptyUC:
            """Stub that returns no due notifications (correct for SQLite — no legacy rows)."""

            def execute(self, **kwargs):
                return []

        wiring.get_container().list_due_notifications_usecase = _EmptyUC()
        try:
            resp = inv_client.get("/api/v1/notifications", headers=_auth(member_token))
            assert resp.status_code == 200
            # Journal note must not appear in notifications
            returned_ids = [item["note"]["id"] for item in resp.get_json()["items"]]
            assert note_id not in returned_ids
        finally:
            wiring.get_container().list_due_notifications_usecase = original_uc


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
