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
        assert items[0]["key"] == company_channel
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
