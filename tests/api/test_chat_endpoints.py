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
        assert resp.get_json() == {"chat": True}

    def test_features_requires_auth(self, inv_client):
        assert inv_client.get("/api/v1/features").status_code == 401

    def test_chat_routes_404_when_flag_off(self, inv_client, member_token, invitation_app):
        invitation_app.config["FEATURE_CHAT"] = False
        try:
            resp = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token))
            assert resp.status_code == 404
            assert resp.get_json()["error"] == "FeatureDisabled"
            assert inv_client.get("/api/v1/features", headers=_auth(member_token)).get_json() == {"chat": False}
        finally:
            invitation_app.config["FEATURE_CHAT"] = True


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


@pytest.fixture
def project_of_a_former_member(invitation_app):
    """A project of the test company created by the outsider, who holds no role in that
    company (a creator who left or was removed), with a leftover assignment row. The
    member is assigned too. Yields the channel key."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app import db
    from app.infrastructure.database.models import ProjectModel, UserModel

    with invitation_app.app_context():
        company_id = db.session.get(ProjectModel, uuid.UUID(invitation_app._test_project_id)).company_id
        outsider_id = db.session.query(UserModel).filter_by(email=invitation_app._test_outsider_email).one().id
        project = ProjectModel(name="Former Member Project", owner_id=outsider_id, company_id=company_id)
        db.session.add(project)
        db.session.flush()
        for uid in (str(outsider_id), invitation_app._test_member_user_id):
            db.session.execute(
                text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:u, :p, :at)"),
                {"u": uid, "p": str(project.id), "at": datetime.now(timezone.utc)},
            )
        db.session.commit()
        project_id = project.id
    yield f"project:{project_id}"
    with invitation_app.app_context():
        db.session.execute(text("DELETE FROM user_projects WHERE project_id = :p"), {"p": str(project_id)})
        db.session.query(ProjectModel).filter_by(id=project_id).delete()
        db.session.commit()


class TestProjectChannelNeedsACompanyRole:
    """Creator or assignee, a user without a role in the project's company is not in its channel."""

    def test_former_member_loses_list_read_and_send(self, inv_client, outsider_token, project_of_a_former_member):
        key = project_of_a_former_member
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(outsider_token)).get_json()["items"]
        assert key not in [c["key"] for c in items]
        assert inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(outsider_token)).status_code == 403
        resp = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "still here?"}, headers=_auth(outsider_token)
        )
        assert resp.status_code == 403

    def test_former_member_is_not_listed_among_the_members(self, inv_client, member_token, project_of_a_former_member):
        key = project_of_a_former_member
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        assert next(c for c in items if c["key"] == key)["member_count"] == 1
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token))
        assert page.status_code == 200
        assert [m["name"] for m in page.get_json()["members"]] == ["member@invite-test.com"]


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
    raises inside the domain entity, so every chat route answers 404 exactly like any
    other unknown channel — old rows are left in the database, simply unreachable."""

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


class TestLegacyAssistantMessages:
    """Rows written by the retired assistant feature stay in the database: listing a
    channel and counting unread must keep working on them."""

    def _add_legacy_rows(self, invitation_app) -> None:
        from app import db
        from app.infrastructure.database.models.chat_message import ChatMessageOrm

        with invitation_app.app_context():
            company_id = uuid.UUID(invitation_app._test_company_id)
            db.session.add(
                ChatMessageOrm(
                    channel_kind="company",
                    channel_id=company_id,
                    sender_id=None,
                    sender_type="assistant",
                    content_type="text",
                    body="Salut !",
                )
            )
            db.session.add(
                ChatMessageOrm(
                    channel_kind="company",
                    channel_id=company_id,
                    sender_id=None,
                    sender_type="assistant",
                    content_type="card",
                    body="Ciment Lafarge 25kg",
                    payload={"card": {"type": "material", "title": "Ciment Lafarge 25kg"}},
                    mentions_assistant=True,
                )
            )
            db.session.commit()

    def test_listing_tolerates_legacy_assistant_rows(self, inv_client, member_token, invitation_app):
        key = f"company:{invitation_app._test_company_id}"
        self._add_legacy_rows(invitation_app)
        resp = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token))
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        card = items[-1]
        assert card["sender_id"] is None
        assert card["sender_name"] == "Folio"
        assert card["sender_type"] == "assistant"
        assert card["content_type"] == "card"
        assert card["mine"] is False
        assert "mentions_assistant" not in card

    def test_legacy_rows_count_as_unread(self, inv_client, member_token, invitation_app):
        key = f"company:{invitation_app._test_company_id}"
        inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(member_token))
        self._add_legacy_rows(invitation_app)
        items = inv_client.get("/api/v1/chat/channels", headers=_auth(member_token)).get_json()["items"]
        assert next(c for c in items if c["key"] == key)["unread_count"] >= 1


class TestReplies:
    def test_mention_token_is_just_text(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            json={"body": "@folio salut"},
            headers=_auth(member_token),
        )
        assert sent.status_code == 201
        assert sent.get_json()["body"] == "@folio salut"

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
            json={"body": "salut", "reply_to_id": other_channel_message["id"]},
            headers=_auth(member_token),
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "BadRequest"


class TestReportAndBlock:
    def _send(self, inv_client, token, key, text):
        resp = inv_client.post(f"/api/v1/chat/channels/{key}/messages", json={"body": text}, headers=_auth(token))
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()

    def test_report_message_is_idempotent_and_member_only(
        self, inv_client, member_token, admin_token, outsider_token, invitation_app
    ):
        key = _project_key(invitation_app)
        msg = self._send(inv_client, member_token, key, "bad words")
        url = f"/api/v1/chat/messages/{msg['id']}/report"
        assert inv_client.post(url, json={"reason": "abuse"}, headers=_auth(admin_token)).status_code == 204
        assert inv_client.post(url, json={"reason": "abuse"}, headers=_auth(admin_token)).status_code == 204
        assert inv_client.post(url, json={}, headers=_auth(outsider_token)).status_code == 403
        missing = inv_client.post(f"/api/v1/chat/messages/{uuid.uuid4()}/report", json={}, headers=_auth(admin_token))
        assert missing.status_code == 404

        from app import db
        from app.infrastructure.database.models import ChatMessageReportOrm

        with invitation_app.app_context():
            rows = db.session.query(ChatMessageReportOrm).filter_by(message_id=uuid.UUID(msg["id"])).all()
            assert len(rows) == 1 and rows[0].reason == "abuse"
            db.session.query(ChatMessageReportOrm).delete()
            db.session.commit()

    def test_block_hides_messages_and_unread_then_unblock_restores(
        self, inv_client, member_token, admin_token, invitation_app
    ):
        key = _project_key(invitation_app)
        member_id = invitation_app._test_member_user_id
        self._send(inv_client, member_token, key, "hello from the blocked one")

        assert inv_client.put(f"/api/v1/chat/blocks/{member_id}", headers=_auth(admin_token)).status_code == 204
        assert inv_client.put(f"/api/v1/chat/blocks/{member_id}", headers=_auth(admin_token)).status_code == 204

        listed = inv_client.get("/api/v1/chat/blocks", headers=_auth(admin_token)).get_json()["items"]
        assert [b["id"] for b in listed] == [member_id]

        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        assert all(m["body"] != "hello from the blocked one" for m in page["items"])
        channels = inv_client.get("/api/v1/chat/channels", headers=_auth(admin_token)).get_json()["items"]
        assert next(c for c in channels if c["key"] == key)["unread_count"] == 0
        # The other direction is untouched: the member still sees the admin's messages.
        self._send(inv_client, admin_token, key, "admin speaks")
        member_page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        assert any(m["body"] == "admin speaks" for m in member_page["items"])

        assert inv_client.delete(f"/api/v1/chat/blocks/{member_id}", headers=_auth(admin_token)).status_code == 204
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        assert any(m["body"] == "hello from the blocked one" for m in page["items"])

    def test_cannot_block_self_or_stranger(self, inv_client, admin_token, outsider_token, invitation_app):
        admin_id = invitation_app._test_admin_user_id
        assert inv_client.put(f"/api/v1/chat/blocks/{admin_id}", headers=_auth(admin_token)).status_code == 400
        stranger = uuid.uuid4()
        assert inv_client.put(f"/api/v1/chat/blocks/{stranger}", headers=_auth(admin_token)).status_code == 404
