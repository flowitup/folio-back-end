"""Integration tests for POST /api/v1/assistant/actions."""

from __future__ import annotations

import uuid

from app.domain.entities.chat_message import ChannelRef
from wiring import get_container


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_key(app) -> str:
    return f"project:{app._test_project_id}"


def _post_choice(app, channel_key: str, addressed_to: str) -> str:
    """Post a choice message into ``channel_key``, addressed to ``addressed_to``;
    returns its id."""
    with app.app_context():
        message = get_container().assistant_messenger.post_choice(
            uuid.UUID(addressed_to),
            "Confirmer le matériau ?",
            [
                {"label": "Confirmer", "action": "confirm", "payload": {}},
                {"label": "Annuler", "action": "cancel", "payload": {}},
            ],
            channel=ChannelRef.parse(channel_key),
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

    def test_reply_to_id_not_addressed_to_caller_403(self, inv_client, member_token, admin_token, invitation_app):
        """The choice lives in a channel `member_token` belongs to, but was addressed to
        the admin — only the addressee may answer it (D18)."""
        choice_id = _post_choice(invitation_app, _project_key(invitation_app), invitation_app._test_admin_user_id)
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "NotAddressed"

    def test_reply_to_id_from_a_channel_the_caller_is_not_a_member_of_403(
        self, inv_client, outsider_token, invitation_app
    ):
        choice_id = _post_choice(invitation_app, _project_key(invitation_app), invitation_app._test_member_user_id)
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "reply_to_id": choice_id},
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "NotAddressed"

    def test_happy_path_marks_answered_and_dispatches(self, inv_client, member_token, invitation_app):
        invitation_app._assistant_dispatcher.actions_received.clear()
        key = _project_key(invitation_app)
        choice_id = _post_choice(invitation_app, key, invitation_app._test_member_user_id)

        # The submitted payload must be byte-for-byte the stored option's own payload
        # (`{}`, see `_post_choice`) — this is what `SubmitAssistantActionUseCase` now
        # requires (review finding C1); the app always resubmits an option unmodified.
        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "payload": {}, "reply_to_id": choice_id},
            headers=_auth(member_token),
        )
        assert resp.status_code == 202
        assert resp.get_json() == {"accepted": True}

        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        choice_message = next(m for m in page["items"] if m["id"] == choice_id)
        assert choice_message["payload"]["answered"] == "confirm"
        assert choice_message["payload"]["answered_payload"] == {}

        user_id = uuid.UUID(invitation_app._test_member_user_id)
        message_id = uuid.UUID(choice_id)
        assert (user_id, message_id, "confirm", {}) in invitation_app._assistant_dispatcher.actions_received

    def test_forged_payload_not_matching_any_option_404s(self, inv_client, member_token, invitation_app):
        """Review finding C1: a client-invented payload that does not byte-for-byte
        match one of the choice's own stored options must never be accepted — this is
        what closed the invoice-delete/S3-read/S3-delete/photo-OCR IDOR family (every
        exploit in that finding relied on the server trusting an arbitrary payload)."""
        choice_id = _post_choice(invitation_app, _project_key(invitation_app), invitation_app._test_member_user_id)

        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "confirm", "payload": {"invoice_id": str(uuid.uuid4())}, "reply_to_id": choice_id},
            headers=_auth(member_token),
        )

        assert resp.status_code == 404
        assert resp.get_json()["error"] == "NotFound"

    def test_forged_action_not_offered_on_the_choice_404s(self, inv_client, member_token, invitation_app):
        choice_id = _post_choice(invitation_app, _project_key(invitation_app), invitation_app._test_member_user_id)

        resp = inv_client.post(
            "/api/v1/assistant/actions",
            json={"action": "delete_everything", "payload": {}, "reply_to_id": choice_id},
            headers=_auth(member_token),
        )

        assert resp.status_code == 404
        assert resp.get_json()["error"] == "NotFound"

    def test_already_answered_409(self, inv_client, member_token, invitation_app):
        choice_id = _post_choice(invitation_app, _project_key(invitation_app), invitation_app._test_member_user_id)
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
