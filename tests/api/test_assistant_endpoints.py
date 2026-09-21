"""Integration tests for POST /api/v1/assistant/actions."""

from __future__ import annotations

import uuid

from wiring import get_container


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _post_choice(app, user_id: str) -> str:
    """Post a choice message into ``user_id``'s assistant channel; returns its id."""
    with app.app_context():
        message = get_container().assistant_messenger.post_choice(
            uuid.UUID(user_id),
            "Confirmer le matériau ?",
            [
                {"label": "Confirmer", "action": "confirm", "payload": {}},
                {"label": "Annuler", "action": "cancel", "payload": {}},
            ],
        )
        return str(message.id)


class TestSubmitAction:
    def test_requires_auth(self, inv_client):
        resp = inv_client.post(
            "/api/v1/assistant/actions", json={"action": "confirm", "reply_to_id": str(uuid.uuid4())}
        )
        assert resp.status_code == 401

    def test_404_when_feature_disabled(self, inv_client, member_token, invitation_app):
        invitation_app.config["FEATURE_ASSISTANT"] = False
        try:
            resp = inv_client.post(
                "/api/v1/assistant/actions",
                json={"action": "confirm", "reply_to_id": str(uuid.uuid4())},
                headers=_auth(member_token),
            )
            assert resp.status_code == 404
            assert resp.get_json()["error"] == "FeatureDisabled"
        finally:
            invitation_app.config["FEATURE_ASSISTANT"] = True

    def test_validation_error_on_empty_action(self, inv_client, member_token):
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "", "reply_to_id": str(uuid.uuid4())},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_validation_error_on_malformed_reply_to_id(self, inv_client, member_token):
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": "not-a-uuid"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 422

    def test_unknown_reply_to_id_404(self, inv_client, member_token):
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": str(uuid.uuid4())},
            headers=_auth(member_token),
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "NotFound"

    def test_reply_to_id_from_someone_elses_assistant_channel_404(
        self, inv_client, member_token, admin_token, invitation_app
    ):
        choice_id = _post_choice(invitation_app, invitation_app._test_admin_user_id)
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert resp.status_code == 404

    def test_happy_path_marks_answered_and_dispatches(self, inv_client, member_token, invitation_app):
        invitation_app._assistant_dispatcher.actions_received.clear()
        choice_id = _post_choice(invitation_app, invitation_app._test_member_user_id)

        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "payload": {"note": "ok"}, "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert resp.status_code == 202
        assert resp.get_json() == {"accepted": True}

        key = f"assistant:{invitation_app._test_member_user_id}"
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        choice_message = next(m for m in page["items"] if m["id"] == choice_id)
        assert choice_message["payload"]["answered"] == "confirm"

        user_id = uuid.UUID(invitation_app._test_member_user_id)
        message_id = uuid.UUID(choice_id)
        assert (user_id, message_id, "confirm", {"note": "ok"}) in invitation_app._assistant_dispatcher.actions_received

    def test_already_answered_409(self, inv_client, member_token, invitation_app):
        choice_id = _post_choice(invitation_app, invitation_app._test_member_user_id)
        first = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert first.status_code == 202
        second = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "cancel", "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert second.status_code == 409
        assert second.get_json()["error"] == "AlreadyAnswered"
