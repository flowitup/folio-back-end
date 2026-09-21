"""Integration tests for the team chat endpoints (FEATURE_CHAT on in the test app)."""

from __future__ import annotations

import io
import uuid
from urllib.parse import quote

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_key(app) -> str:
    return f"project:{app._test_project_id}"


@pytest.fixture
def company_channel(invitation_app):
    """A company attached to the member and admin users; returns its channel key."""
    from app import db
    from app.infrastructure.database.models import CompanyModel, UserCompanyAccessModel

    with invitation_app.app_context():
        company = CompanyModel(
            legal_name="AVN Construction",
            address="1 rue du Chantier",
            created_by=uuid.UUID(invitation_app._test_admin_user_id),
        )
        db.session.add(company)
        db.session.flush()
        for uid in (invitation_app._test_member_user_id, invitation_app._test_admin_user_id):
            db.session.add(UserCompanyAccessModel(user_id=uuid.UUID(uid), company_id=company.id, role="member"))
        db.session.commit()
        key = f"company:{company.id}"
    yield key
    with invitation_app.app_context():
        db.session.query(UserCompanyAccessModel).filter_by(company_id=uuid.UUID(key.split(":")[1])).delete()
        db.session.query(CompanyModel).filter_by(id=uuid.UUID(key.split(":")[1])).delete()
        db.session.commit()


class TestFeatures:
    def test_features_reports_chat_flag(self, inv_client, member_token):
        resp = inv_client.get("/api/v1/features", headers=_auth(member_token))
        assert resp.status_code == 200
        assert resp.get_json() == {"chat": True, "assistant": True}

    def test_features_requires_auth(self, inv_client):
        assert inv_client.get("/api/v1/features").status_code == 401

    def test_chat_routes_404_when_flag_off(self, inv_client, member_token, invitation_app):
        invitation_app.config["FEATURE_CHAT"] = False
        try:
            resp = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token))
            assert resp.status_code == 404
            assert resp.get_json()["error"] == "FeatureDisabled"
            assert inv_client.get("/api/v1/features", headers=_auth(member_token)).get_json() == {
                "chat": False,
                "assistant": True,
            }
        finally:
            invitation_app.config["FEATURE_CHAT"] = True

    def test_features_reports_assistant_off_without_both_keys(self, inv_client, member_token, invitation_app):
        invitation_app.config["TYPESAFE_API_KEY"] = ""
        try:
            resp = inv_client.get("/api/v1/features", headers=_auth(member_token))
            assert resp.get_json() == {"chat": True, "assistant": False}
        finally:
            invitation_app.config["TYPESAFE_API_KEY"] = "test-typesafe-key"


class TestChannels:
    def test_member_sees_project_channel(self, inv_client, member_token, invitation_app):
        resp = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token))
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        keys = [c["key"] for c in items]
        assert _project_key(invitation_app) in keys
        project = next(c for c in items if c["key"] == _project_key(invitation_app))
        assert project["kind"] == "project"
        assert project["name"] == "Invite Test Project"
        # admin (owner) + member + target user
        assert project["member_count"] == 3
        assert project["unread_count"] == 0

    def test_company_channel_listed_first(self, inv_client, member_token, company_channel):
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        # Company channels are sorted by legal name; "AVN Construction" sorts before
        # "Invite Test Company".
        assert items[0]["key"] == company_channel
        assert items[0]["kind"] == "company"
        assert items[0]["name"] == "AVN Construction"
        assert items[0]["member_count"] == 2

    def test_outsider_sees_no_project_channel(self, inv_client, outsider_token, invitation_app):
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(outsider_token)).get_json()["items"]
        assert _project_key(invitation_app) not in [c["key"] for c in items]


class TestMessages:
    def test_send_list_and_unread_flow(self, inv_client, member_token, admin_token, invitation_app):
        key = _project_key(invitation_app)
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "Sáng nay đổ xong sàn mái"},
            headers=_auth(member_token),
        )
        assert sent.status_code == 201, sent.get_json()
        data = sent.get_json()
        assert data["body"] == "Sáng nay đổ xong sàn mái"
        assert data["mine"] is True
        assert data["attachment"] is None
        assert data["sender_name"] == "member@invite-test.com"

        # The owner (admin) sees one unread message, the sender none.
        admin_channels = inv_client.get("/api/v1/chat/channels", headers=_auth(admin_token)).get_json()["items"]
        assert next(c for c in admin_channels if c["key"] == key)["unread_count"] == 1
        member_channels = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        assert next(c for c in member_channels if c["key"] == key)["unread_count"] == 0

        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token))
        assert page.status_code == 200
        body = page.get_json()
        assert body["items"][-1]["body"] == "Sáng nay đổ xong sàn mái"
        assert body["items"][-1]["mine"] is False
        assert {m["name"] for m in body["members"]} >= {"member@invite-test.com", "admin@invite-test.com"}

        assert inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(admin_token)).status_code == 204
        admin_channels = inv_client.get("/api/v1/chat/channels", headers=_auth(admin_token)).get_json()["items"]
        assert next(c for c in admin_channels if c["key"] == key)["unread_count"] == 0

    def test_members_expose_read_markers_for_seen_receipts(self, inv_client, member_token, admin_token, invitation_app):
        key = _project_key(invitation_app)
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "seen?"}, headers=_auth(member_token)
        )
        assert sent.status_code == 201
        # The sender's marker moved to the message time; the admin has not opened the channel yet.
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        by_name = {m["name"]: m["last_read_at"] for m in page["members"]}
        assert by_name["member@invite-test.com"] >= sent.get_json()["created_at"]

        assert inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(admin_token)).status_code == 204
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        by_name = {m["name"]: m["last_read_at"] for m in page["members"]}
        assert by_name["admin@invite-test.com"] is not None
        assert by_name["admin@invite-test.com"] >= sent.get_json()["created_at"]

    def test_pagination_with_before(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        for i in range(3):
            inv_client.post(
                f"/api/v1/chat/channels/{key}/messages", json={"body": f"m{i}"}, headers=_auth(member_token)
            )
        latest = inv_client.get(f"/api/v1/chat/channels/{key}/messages?limit=1", headers=_auth(member_token)).get_json()
        assert len(latest["items"]) == 1
        older = inv_client.get(
            f"/api/v1/chat/channels/{key}/messages?limit=1&before={quote(latest['items'][0]['created_at'])}",
            headers=_auth(member_token),
        ).get_json()
        assert len(older["items"]) == 1
        assert older["items"][0]["created_at"] < latest["items"][0]["created_at"]

    def test_outsider_forbidden(self, inv_client, outsider_token, invitation_app):
        key = _project_key(invitation_app)
        assert inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(outsider_token)).status_code == 403
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "hi"}, headers=_auth(outsider_token)
        )
        assert resp.status_code == 403

    def test_unknown_and_malformed_channel(self, inv_client, member_token):
        assert (
            inv_client.get(
                f"/api/v1/chat/channels/project:{uuid.uuid4()}/messages", headers=_auth(member_token)
            ).status_code
            == 404
        )
        assert inv_client.get("/api/v1/chat/channels/nope/messages", headers=_auth(member_token)).status_code == 404

    def test_empty_body_rejected(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "   "}, headers=_auth(member_token)
        )
        assert resp.status_code in (400, 422)

    def test_company_channel_messages(self, inv_client, member_token, admin_token, outsider_token, company_channel):
        resp = inv_client.post(
            f"/api/v1/chat/channels/{company_channel}/messages",
            json={"body": "Chào cả công ty"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        page = inv_client.get(
            f"/api/v1/chat/channels/{company_channel}/messages", headers=_auth(member_token)
        ).get_json()
        assert page["items"][-1]["body"] == "Chào cả công ty"
        assert (
            inv_client.get(
                f"/api/v1/chat/channels/{company_channel}/messages", headers=_auth(outsider_token)
            ).status_code
            == 403
        )


class TestAttachments:
    def test_image_attachment_roundtrip(self, inv_client, member_token, admin_token, outsider_token, invitation_app):
        key = _project_key(invitation_app)
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"body": "Ảnh sàn mái", "file": (io.BytesIO(png), "IMG_2041.png", "image/png")},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()
        assert data["attachment"]["filename"] == "IMG_2041.png"
        assert data["attachment"]["size_bytes"] == len(png)
        url = data["attachment"]["url"]

        download = inv_client.get(url, headers=_auth(admin_token))
        assert download.status_code == 200
        assert download.data == png
        assert download.mimetype == "image/png"
        assert inv_client.get(url, headers=_auth(outsider_token)).status_code == 403

    def test_image_only_message_allowed(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"file": (io.BytesIO(b"\xff\xd8\xff" + b"1" * 10), "a.jpg", "image/jpeg")},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["body"] is None

    def test_voice_note_roundtrip(self, inv_client, member_token, admin_token, invitation_app):
        key = _project_key(invitation_app)
        m4a = b"\x00\x00\x00\x20ftypM4A " + b"0" * 64
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"file": (io.BytesIO(m4a), "voice-1757630000.m4a", "audio/m4a")},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201, resp.get_json()
        attachment = resp.get_json()["attachment"]
        assert attachment["content_type"] == "audio/m4a"
        assert resp.get_json()["body"] is None

        download = inv_client.get(attachment["url"], headers=_auth(admin_token))
        assert download.status_code == 200
        assert download.data == m4a
        assert download.mimetype == "audio/m4a"

    @pytest.mark.parametrize("content_type", ["audio/aac", "audio/x-m4a", "audio/mp4", "audio/mp4a-latm", "audio/mpeg"])
    def test_every_recorder_spelling_accepted(self, inv_client, member_token, invitation_app, content_type):
        """iOS and Android label the same AAC recording differently; all of them must pass."""
        key = _project_key(invitation_app)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"file": (io.BytesIO(b"\x00\x00\x00\x20ftypM4A " + b"1" * 32), "voice.m4a", content_type)},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201, resp.get_json()

    def test_unsupported_type_rejected(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"file": (io.BytesIO(b"%PDF-1.4"), "doc.pdf", "application/pdf")},
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 415

    def test_missing_attachment_404(self, inv_client, member_token):
        assert (
            inv_client.get(f"/api/v1/chat/messages/{uuid.uuid4()}/attachment", headers=_auth(member_token)).status_code
            == 404
        )


class TestRetiredAssistantChannel:
    """The old per-user `assistant:<user_id>` channel is retired: parsing that key
    raises inside the domain entity, so every chat route (and `/assistant/actions`,
    covered in test_assistant_endpoints.py) answers 404 exactly like any other unknown
    channel — old rows are left in the database, simply unreachable."""

    def _assistant_key(self, user_id: str) -> str:
        return f"assistant:{user_id}"

    def test_list_messages_404(self, inv_client, member_token, invitation_app):
        key = self._assistant_key(invitation_app._test_member_user_id)
        resp = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token))
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "NotFound"

    def test_send_message_404(self, inv_client, member_token, invitation_app):
        key = self._assistant_key(invitation_app._test_member_user_id)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "bonjour"}, headers=_auth(member_token)
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "NotFound"

    def test_mark_read_404(self, inv_client, member_token, invitation_app):
        key = self._assistant_key(invitation_app._test_member_user_id)
        resp = inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(member_token))
        assert resp.status_code == 404

    def test_not_listed(self, inv_client, member_token):
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        assert "assistant" not in [c["kind"] for c in items]


class TestAdminChannel:
    """`admin:<company_id>` — a company's admins + platform ops (D17)."""

    def _admin_key(self, company_id: str) -> str:
        return f"admin:{company_id}"

    def test_listed_for_admin_not_for_member(self, inv_client, admin_token, member_token, invitation_app):
        key = self._admin_key(invitation_app._test_company_id)
        admin_items = inv_client.get("/api/v1/chat/channels", headers=_auth(admin_token)).get_json()["items"]
        admin_channel = next(c for c in admin_items if c["key"] == key)
        assert admin_channel["kind"] == "admin"
        assert admin_channel["name"] == "Invite Test Company"

        member_items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        assert key not in [c["key"] for c in member_items]

    def test_admin_channel_right_after_its_company_channel(self, inv_client, admin_token, invitation_app):
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(admin_token)).get_json()["items"]
        keys = [c["key"] for c in items]
        company_index = keys.index(f"company:{invitation_app._test_company_id}")
        assert keys[company_index + 1] == self._admin_key(invitation_app._test_company_id)

    def test_member_forbidden(self, inv_client, member_token, invitation_app):
        key = self._admin_key(invitation_app._test_company_id)
        assert inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).status_code == 403
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "hi"}, headers=_auth(member_token)
        )
        assert resp.status_code == 403

    def test_admin_can_read_and_send(self, inv_client, admin_token, invitation_app):
        key = self._admin_key(invitation_app._test_company_id)
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "Bilan du mois"}, headers=_auth(admin_token)
        )
        assert sent.status_code == 201, sent.get_json()
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        assert page["items"][-1]["body"] == "Bilan du mois"

    def test_platform_ops_can_access_without_a_company_role(self, inv_client, superadmin_token, invitation_app):
        key = self._admin_key(invitation_app._test_company_id)
        resp = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(superadmin_token))
        assert resp.status_code == 200

    def test_unknown_company_404(self, inv_client, admin_token):
        key = self._admin_key(str(uuid.uuid4()))
        resp = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_platform_ops_sees_the_admin_channel_in_the_chip_row(self, inv_client, superadmin_token, invitation_app):
        """Phase 03's answer to phase 01/02's open question 1: ops oversees every
        company, not just the ones it happens to hold a `user_company_access` row for."""
        key = self._admin_key(invitation_app._test_company_id)
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(superadmin_token)).get_json()["items"]
        admin_channel = next(c for c in items if c["key"] == key)
        assert admin_channel["kind"] == "admin"
        # Ops is not itself a member of the company, so the company channel is absent —
        # only its admin channel is listed.
        assert f"company:{invitation_app._test_company_id}" not in [c["key"] for c in items]


class TestFolioReplies:
    """`AssistantMessenger` posting into a shared channel — sender name, unread counts,
    and the `card` wire contract, all through the same `GET .../messages` the apps poll."""

    def _channel(self, invitation_app) -> str:
        return f"company:{invitation_app._test_company_id}"

    def test_reply_lands_in_the_target_channel_with_folio_as_sender(self, inv_client, member_token, invitation_app):
        from app.domain.entities.chat_message import ChannelRef
        from wiring import get_container

        key = self._channel(invitation_app)
        with invitation_app.app_context():
            get_container().assistant_messenger.post_text(
                uuid.UUID(invitation_app._test_member_user_id),
                "Salut !",
                channel=ChannelRef(kind="company", id=uuid.UUID(invitation_app._test_company_id)),
            )
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        reply = page["items"][-1]
        assert reply["sender_id"] is None
        assert reply["sender_name"] == "Folio"
        assert reply["sender_type"] == "assistant"
        assert reply["mine"] is False

    def test_reply_visible_to_every_channel_member(self, inv_client, member_token, admin_token, invitation_app):
        """The assistant answered a mention in a shared channel — every member (not
        just the asker) sees the reply, same as a human message (D19)."""
        from app.domain.entities.chat_message import ChannelRef
        from wiring import get_container

        key = self._channel(invitation_app)
        with invitation_app.app_context():
            get_container().assistant_messenger.post_text(
                uuid.UUID(invitation_app._test_member_user_id),
                "Voici la réponse pour toute l'équipe.",
                channel=ChannelRef(kind="company", id=uuid.UUID(invitation_app._test_company_id)),
            )
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        assert page["items"][-1]["body"] == "Voici la réponse pour toute l'équipe."

    def test_unread_count_includes_the_reply(self, inv_client, member_token, invitation_app):
        from app.domain.entities.chat_message import ChannelRef
        from wiring import get_container

        key = self._channel(invitation_app)
        inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(member_token))
        with invitation_app.app_context():
            get_container().assistant_messenger.post_text(
                uuid.UUID(invitation_app._test_member_user_id),
                "Bonjour !",
                channel=ChannelRef(kind="company", id=uuid.UUID(invitation_app._test_company_id)),
            )
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        company_channel = next(c for c in items if c["key"] == key)
        assert company_channel["unread_count"] >= 1

    def test_post_card_round_trips_with_the_wire_contract_shape(self, inv_client, member_token, invitation_app):
        """A real `AssistantMessenger.post_card(...)` call, read back through the same
        `GET .../messages` endpoint the app polls — proves the app's parser actually
        receives `{"card": {type, id, project_id, title, subtitle, badge, thumbnail_url,
        extra}}`, not the pre-fix ad-hoc shape (second addendum: the app's parser
        rejected every real card and silently fell back to text)."""
        from app.domain.entities.chat_message import ChannelRef
        from wiring import get_container

        key = self._channel(invitation_app)
        product_id = uuid.uuid4()
        with invitation_app.app_context():
            get_container().assistant_messenger.post_card(
                uuid.UUID(invitation_app._test_member_user_id),
                card_type="material",
                entity_id=product_id,
                title="Ciment Lafarge 25kg",
                subtitle="Confirmé",
                badge="confirmed",
                thumbnail_url="/api/v1/bibliotheque/products/x/image",
                extra={"has_image": True},
                channel=ChannelRef(kind="company", id=uuid.UUID(invitation_app._test_company_id)),
            )
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        card_message = page["items"][-1]
        assert card_message["content_type"] == "card"
        assert card_message["payload"]["card"] == {
            "type": "material",
            "id": str(product_id),
            "project_id": None,
            "title": "Ciment Lafarge 25kg",
            "subtitle": "Confirmé",
            "badge": "confirmed",
            "thumbnail_url": "/api/v1/bibliotheque/products/x/image",
            "extra": {"has_image": True},
        }


class TestMentionDispatch:
    """Send-time `@folio` detection / reply-to-assistant detection and the dispatch gate
    (D18) — never other chat reaches the assistant pipeline."""

    def test_message_without_mention_never_dispatches(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        invitation_app._assistant_dispatcher.messages_received.clear()
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "Sáng nay đổ xong sàn mái"},
            headers=_auth(member_token),
        )
        assert sent.status_code == 201
        assert sent.get_json()["mentions_assistant"] is False
        assert invitation_app._assistant_dispatcher.messages_received == []

    def test_mention_dispatches_case_insensitively(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        invitation_app._assistant_dispatcher.messages_received.clear()
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "@Folio combien on a dépensé ?"},
            headers=_auth(member_token),
        )
        assert sent.status_code == 201
        data = sent.get_json()
        assert data["mentions_assistant"] is True
        message_id = uuid.UUID(data["id"])
        user_id = uuid.UUID(invitation_app._test_member_user_id)
        assert (user_id, message_id) in invitation_app._assistant_dispatcher.messages_received

    def test_reply_to_an_assistant_message_dispatches_without_the_token(self, inv_client, member_token, invitation_app):
        from app.domain.entities.chat_message import ChannelRef
        from wiring import get_container

        key = _project_key(invitation_app)
        with invitation_app.app_context():
            assistant_reply = get_container().assistant_messenger.post_text(
                uuid.UUID(invitation_app._test_member_user_id),
                "Tu veux dire quoi exactement ?",
                channel=ChannelRef(kind="project", id=uuid.UUID(invitation_app._test_project_id)),
            )
        invitation_app._assistant_dispatcher.messages_received.clear()
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "le budget travaux", "reply_to_id": str(assistant_reply.id)},
            headers=_auth(member_token),
        )
        assert sent.status_code == 201, sent.get_json()
        data = sent.get_json()
        assert data["mentions_assistant"] is True
        assert data["reply_to_id"] == str(assistant_reply.id)
        message_id = uuid.UUID(data["id"])
        user_id = uuid.UUID(invitation_app._test_member_user_id)
        assert (user_id, message_id) in invitation_app._assistant_dispatcher.messages_received

    def test_photo_caption_with_mention_dispatches(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        invitation_app._assistant_dispatcher.messages_received.clear()
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={
                "body": "@folio c'est quoi ce matériau ?",
                "file": (io.BytesIO(b"\xff\xd8\xff" + b"1" * 10), "a.jpg", "image/jpeg"),
            },
            content_type="multipart/form-data",
            headers=_auth(member_token),
        )
        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()
        assert data["mentions_assistant"] is True
        message_id = uuid.UUID(data["id"])
        user_id = uuid.UUID(invitation_app._test_member_user_id)
        assert (user_id, message_id) in invitation_app._assistant_dispatcher.messages_received

    def test_reply_to_id_from_another_channel_400s(
        self, inv_client, member_token, admin_token, invitation_app, company_channel
    ):
        other_channel_message = inv_client.post(
            f"/api/v1/chat/channels/{company_channel}/messages",
            json={"body": "message dans un autre channel"},
            headers=_auth(admin_token),
        ).get_json()

        key = _project_key(invitation_app)
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "@folio salut", "reply_to_id": other_channel_message["id"]},
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "BadRequest"
