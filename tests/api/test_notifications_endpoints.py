"""Integration tests for user-scoped notification endpoints (2 routes).

Covers:
  GET  /api/v1/notifications              — list due notifications
  POST /api/v1/notifications/<id>/dismiss — dismiss a notification

Critical assertions:
  - Cache-Control: no-cache, must-revalidate header on GET response
  - 401 without JWT
  - 404 dismiss of note in a project where user is not a member (same as a missing note)
  - 404 dismiss of non-existent note
  - 204 on successful dismiss
  - Idempotent dismiss (second call is 204, not 4xx)

NOTE: GET /notifications tests that invoke list_due_for_user are skipped on
SQLite because the query uses Postgres-specific syntax (::timestamp, AT TIME ZONE,
INTERVAL). These tests run green against a Postgres TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid

import pytest

# Marker applied to tests that require Postgres SQL syntax in list_due_for_user.
_needs_pg = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="list_due_for_user uses Postgres-specific SQL — not compatible with SQLite test DB",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_NOTIFICATIONS_URL = "/api/v1/notifications"


def _dismiss_url(note_id: str) -> str:
    return f"/api/v1/notifications/{note_id}/dismiss"


# ===========================================================================
# GET /api/v1/notifications
# ===========================================================================


class TestListNotificationsEndpoint:
    @_needs_pg
    def test_200_returns_items_and_count(self, inv_client, member_token):
        resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)
        assert data["count"] == len(data["items"])

    @_needs_pg
    def test_200_cache_control_header_present(self, inv_client, member_token):
        """CRITICAL: Cache-Control: no-cache, must-revalidate must be set."""
        resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cc
        assert "must-revalidate" in cc

    def test_401_unauthenticated(self, inv_client):
        resp = inv_client.get(_NOTIFICATIONS_URL)
        assert resp.status_code == 401

    @_needs_pg
    def test_200_item_shape_when_notifications_present(self, inv_client, member_token, invitation_app, note_legacy_due):
        """A legacy due row (due_date past, lead_time=0, status=open) surfaces in GET /notifications.

        Asserts the serialized item shape: outer 'note'+'dismissed' envelope, plus
        key fields including 'category' (added by the journal pivot serializer).
        The loop is guaranteed to iterate because note_legacy_due is a qualifying row.
        """
        resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) >= 1, "Expected at least one due notification from note_legacy_due fixture"
        for item in items:
            assert "note" in item
            assert "dismissed" in item
            assert "id" in item["note"]
            assert "title" in item["note"]
            assert "category" in item["note"]

    @_needs_pg
    def test_200_count_matches_items_length(self, inv_client, member_token):
        resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == len(data["items"])

    def test_each_reminder_carries_its_due_date(self, inv_client, member_token, monkeypatch):
        """The bell (web and mobile) shows when a reminder is for, so an overdue one stands out."""
        from datetime import date, datetime, timezone

        import wiring
        from app.application.notes.dtos import DueNotificationDto, NoteDto

        now = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
        dto = DueNotificationDto(
            note=NoteDto(
                id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                created_by=uuid.uuid4(),
                title="Permit renewal",
                description=None,
                category="general",
                status="open",
                created_at=now,
                updated_at=now,
            ),
            due_date=date(2026, 10, 8),
            lead_time_minutes=1440,
        )

        class _Due:
            def execute(self, **_kwargs):
                return [dto]

        monkeypatch.setattr(wiring.get_container(), "list_due_notifications_usecase", _Due())
        resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
        assert resp.status_code == 200
        note = resp.get_json()["items"][0]["note"]
        assert note["due_date"] == "2026-10-08"
        assert note["lead_time_minutes"] == 1440

    def test_500_on_unexpected_exception(self, inv_client, member_token, monkeypatch):
        """Monkeypatching the use-case ensures the 500 path is hit regardless of DB dialect."""
        import wiring

        original = wiring.get_container().list_due_notifications_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Boom")

        monkeypatch.setattr(wiring.get_container(), "list_due_notifications_usecase", _Broken())
        try:
            resp = inv_client.get(_NOTIFICATIONS_URL, headers=_auth(member_token))
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "list_due_notifications_usecase", original)


# ===========================================================================
# POST /api/v1/notifications/<note_id>/dismiss
# ===========================================================================


class TestDismissNotificationEndpoint:
    def test_204_member_dismisses_note(self, inv_client, member_token, invitation_app, note_open):
        resp = inv_client.post(
            _dismiss_url(note_open),
            headers=_auth(member_token),
        )
        assert resp.status_code == 204
        assert resp.data == b""

    def test_204_idempotent_dismiss_twice(self, inv_client, member_token, invitation_app, note_open):
        """Second dismiss of the same note must also return 204, not 409/500."""
        inv_client.post(_dismiss_url(note_open), headers=_auth(member_token))
        resp = inv_client.post(_dismiss_url(note_open), headers=_auth(member_token))
        assert resp.status_code == 204

    def test_401_unauthenticated(self, inv_client, invitation_app, note_open):
        resp = inv_client.post(_dismiss_url(note_open))
        assert resp.status_code == 401

    def test_404_non_member_cannot_dismiss_note_in_other_project(
        self, inv_client, non_member_token, invitation_app, note_other_project
    ):
        """A note of a project the user is not on answers like a missing one: existence is not leaked."""
        note_id, _project_id = note_other_project
        resp = inv_client.post(
            _dismiss_url(note_id),
            headers=_auth(non_member_token),
        )
        assert resp.status_code == 404
        missing = inv_client.post(_dismiss_url(str(uuid.uuid4())), headers=_auth(non_member_token))
        assert resp.get_json() == missing.get_json()

    def test_404_nonexistent_note(self, inv_client, member_token):
        resp = inv_client.post(
            _dismiss_url(str(uuid.uuid4())),
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_500_on_unexpected_exception(self, inv_client, member_token, note_open, monkeypatch):
        import wiring

        original = wiring.get_container().dismiss_notification_usecase

        class _Broken:
            def execute(self, **_kwargs):
                raise RuntimeError("Crash")

        monkeypatch.setattr(wiring.get_container(), "dismiss_notification_usecase", _Broken())
        try:
            resp = inv_client.post(_dismiss_url(note_open), headers=_auth(member_token))
            assert resp.status_code == 500
            assert resp.get_json()["error"] == "InternalError"
        finally:
            monkeypatch.setattr(wiring.get_container(), "dismiss_notification_usecase", original)
