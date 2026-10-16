"""Chat QA round 3: platform ops seat, paging past blocked senders, reactions as the reader sees
them, reports that reach the admins, unread from join time, attachment bytes, channel labels."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_key(app) -> str:
    return f"project:{app._test_project_id}"


def _send(client, token, key, text):
    resp = client.post(f"/api/v1/chat/channels/{key}/messages", json={"body": text}, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _channels(client, token):
    return {c["key"]: c for c in client.get("/api/v1/chat/channels", headers=_auth(token)).get_json()["items"]}


@pytest.fixture
def chat_tables_clean(invitation_app):
    yield
    from app import db
    from app.infrastructure.database.models import ChatMessageReportOrm
    from app.infrastructure.database.models.chat_message import (
        ChatChannelReadOrm,
        ChatMessageOrm,
        ChatMessageReactionOrm,
        ChatUserBlockOrm,
    )

    with invitation_app.app_context():
        for model in (ChatMessageReactionOrm, ChatMessageReportOrm, ChatUserBlockOrm, ChatChannelReadOrm):
            db.session.query(model).delete()
        db.session.query(ChatMessageOrm).delete()
        db.session.commit()


@pytest.mark.usefixtures("chat_tables_clean")
class TestPlatformOpsHasNoImplicitSeat:
    def test_ops_outside_the_company_cannot_list_read_or_post(self, inv_client, superadmin_token, invitation_app):
        key = _project_key(invitation_app)
        assert key not in _channels(inv_client, superadmin_token)
        assert (
            inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(superadmin_token)).status_code == 403
        )
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages", json={"body": "support"}, headers=_auth(superadmin_token)
        )
        assert sent.status_code == 403
        assert inv_client.post(f"/api/v1/chat/channels/{key}/read", headers=_auth(superadmin_token)).status_code == 403


@pytest.mark.usefixtures("chat_tables_clean")
class TestPagingPastBlockedSenders:
    def test_a_page_holds_visible_messages_and_before_reaches_older_ones(
        self, inv_client, member_token, admin_token, invitation_app
    ):
        key = _project_key(invitation_app)
        for i in range(4):
            _send(inv_client, admin_token, key, f"visible {i}")
        for i in range(12):
            _send(inv_client, member_token, key, f"spam {i}")
        assert (
            inv_client.put(
                f"/api/v1/chat/blocks/{invitation_app._test_member_user_id}", headers=_auth(admin_token)
            ).status_code
            == 204
        )
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages?limit=3", headers=_auth(admin_token)).get_json()
        assert [m["body"] for m in page["items"]] == ["visible 1", "visible 2", "visible 3"]
        older = inv_client.get(
            f"/api/v1/chat/channels/{key}/messages",
            query_string={"limit": 3, "before": page["items"][0]["created_at"]},
            headers=_auth(admin_token),
        ).get_json()
        assert [m["body"] for m in older["items"]] == ["visible 0"]


@pytest.mark.usefixtures("chat_tables_clean")
class TestReactionsAsTheReaderSeesThem:
    def test_put_and_delete_hide_blocked_reactors_and_name_every_reactor(
        self, inv_client, member_token, admin_token, invitation_app
    ):
        key = _project_key(invitation_app)
        msg = _send(inv_client, member_token, key, "salut")
        url = f"/api/v1/chat/messages/{msg['id']}/reaction"
        assert inv_client.put(url, json={"emoji": "\U0001f44d"}, headers=_auth(admin_token)).status_code == 200
        inv_client.put(f"/api/v1/chat/blocks/{invitation_app._test_admin_user_id}", headers=_auth(member_token))

        put = inv_client.put(url, json={"emoji": "❤️"}, headers=_auth(member_token)).get_json()
        assert [r["user_id"] for r in put["reactions"]] == [invitation_app._test_member_user_id]
        assert put["reactions"][0]["user_name"] == "member@invite-test.com"
        removed = inv_client.delete(url, headers=_auth(member_token)).get_json()
        assert removed["reactions"] == []

        # The other direction is untouched and names the reactor.
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        mine = next(m for m in page["items"] if m["id"] == msg["id"])
        assert [(r["user_name"], r["emoji"]) for r in mine["reactions"]] == [("admin@invite-test.com", "\U0001f44d")]

    def test_changing_a_reaction_keeps_one_row(self, inv_client, member_token, admin_token, invitation_app):
        key = _project_key(invitation_app)
        msg = _send(inv_client, member_token, key, "bonjour")
        url = f"/api/v1/chat/messages/{msg['id']}/reaction"
        for emoji in ("\U0001f44d", "\U0001f602", "\U0001f602"):
            resp = inv_client.put(url, json={"emoji": emoji}, headers=_auth(admin_token))
            assert resp.status_code == 200
        assert [r["emoji"] for r in resp.get_json()["reactions"]] == ["\U0001f602"]


@pytest.mark.usefixtures("chat_tables_clean")
class TestReportsReachTheAdmins:
    @pytest.fixture
    def dispatched(self, invitation_app):
        """Swap in a real ChatReportNotifier over a recording dispatcher."""
        from app.application.push.chat_report_notifier import ChatReportNotifier
        from wiring import get_container

        calls: list = []

        class _Dispatcher:
            def dispatch(self, **kwargs):
                calls.append(kwargs)

        with invitation_app.app_context():
            usecase = get_container().report_chat_message_usecase
            previous = usecase.notifier
            usecase.notifier = ChatReportNotifier(dispatcher=_Dispatcher(), directory=get_container().chat_repo)
        yield calls
        usecase.notifier = previous

    def test_own_message_cannot_be_reported(self, inv_client, member_token, invitation_app):
        msg = _send(inv_client, member_token, _project_key(invitation_app), "mine")
        resp = inv_client.post(f"/api/v1/chat/messages/{msg['id']}/report", json={}, headers=_auth(member_token))
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "CannotReportOwnMessage"

    def test_report_notifies_admins_lists_and_dismisses(
        self, inv_client, member_token, admin_token, outsider_token, invitation_app, dispatched
    ):
        key = _project_key(invitation_app)
        # The target user writes, the member reports: the admin (neither of them) is told.
        from tests.auth_login_helper import mint_access_token

        target_token = mint_access_token(inv_client, invitation_app._test_target_user_email)
        msg = _send(inv_client, target_token, key, "propos déplacés")
        url = f"/api/v1/chat/messages/{msg['id']}/report"
        assert inv_client.post(url, json={"reason": "insulte"}, headers=_auth(member_token)).status_code == 204
        assert inv_client.post(url, json={"reason": "insulte"}, headers=_auth(member_token)).status_code == 204

        # One notification: the repeated report of the same message is not announced again.
        assert len(dispatched) == 1
        call = dispatched[0]
        assert list(call["recipients"]) == [uuid.UUID(invitation_app._test_admin_user_id)]
        title, body = call["render"]("fr")
        assert title == "Message signalé"
        assert body == (
            "member@invite-test.com a signalé un message de Target User · Invite Test Project : "
            "« propos déplacés » — Motif : insulte"
        )
        assert call["data"]["kind"] == "chat_report"
        assert call["data"]["channel_key"] == key and call["data"]["message_id"] == msg["id"]

        company = invitation_app._test_company_id
        assert (
            inv_client.get("/api/v1/chat/reports", query_string={"company_id": company}, headers=_auth(member_token))
        ).status_code == 403
        listed = inv_client.get(
            "/api/v1/chat/reports", query_string={"company_id": company}, headers=_auth(admin_token)
        ).get_json()["items"]
        assert len(listed) == 1
        report = listed[0]
        assert report["message"]["body"] == "propos déplacés"
        assert report["reporter_name"] == "member@invite-test.com"
        assert report["reason"] == "insulte"
        assert report["channel_name"] == "Invite Test Project"

        resolve = f"/api/v1/chat/reports/{report['id']}/resolve"
        assert inv_client.post(resolve, json={"action": "dismiss"}, headers=_auth(outsider_token)).status_code == 404
        assert inv_client.post(resolve, json={"action": "nope"}, headers=_auth(admin_token)).status_code == 422
        assert inv_client.post(resolve, json={"action": "dismiss"}, headers=_auth(admin_token)).status_code == 204
        assert (
            inv_client.get(
                "/api/v1/chat/reports", query_string={"company_id": company}, headers=_auth(admin_token)
            ).get_json()["items"]
            == []
        )
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
        assert any(m["id"] == msg["id"] for m in page["items"])

    def test_delete_message_removes_it_for_everyone(self, inv_client, member_token, admin_token, invitation_app):
        key = _project_key(invitation_app)
        msg = _send(inv_client, member_token, key, "à supprimer")
        inv_client.put(
            f"/api/v1/chat/messages/{msg['id']}/reaction", json={"emoji": "\U0001f44d"}, headers=_auth(admin_token)
        )
        assert (
            inv_client.post(f"/api/v1/chat/messages/{msg['id']}/report", json={}, headers=_auth(admin_token))
        ).status_code == 204
        report = inv_client.get(
            "/api/v1/chat/reports",
            query_string={"company_id": invitation_app._test_company_id},
            headers=_auth(admin_token),
        ).get_json()["items"][0]
        resp = inv_client.post(
            f"/api/v1/chat/reports/{report['id']}/resolve",
            json={"action": "delete_message"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204
        page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(member_token)).get_json()
        assert all(m["id"] != msg["id"] for m in page["items"])


@pytest.mark.usefixtures("chat_tables_clean")
class TestUnreadStartsAtJoin:
    def test_history_before_joining_is_not_unread(self, inv_client, admin_token, invitation_app):
        from app import db
        from app.infrastructure.database.models import UserCompanyAccessModel, UserModel
        from tests.auth_login_helper import mint_access_token

        key = f"company:{invitation_app._test_company_id}"
        for i in range(3):
            _send(inv_client, admin_token, key, f"old {i}")
        with invitation_app.app_context():
            user = UserModel(email="newcomer@invite-test.com", is_active=True, display_name="Newcomer")
            db.session.add(user)
            db.session.flush()
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user.id,
                    company_id=uuid.UUID(invitation_app._test_company_id),
                    role="member",
                    attached_at=datetime.now(timezone.utc) + timedelta(seconds=1),
                )
            )
            db.session.commit()
            user_id = user.id
        try:
            token = mint_access_token(inv_client, "newcomer@invite-test.com")
            assert _channels(inv_client, token)[key]["unread_count"] == 0
        finally:
            with invitation_app.app_context():
                db.session.query(UserCompanyAccessModel).filter_by(user_id=user_id).delete()
                db.session.query(UserModel).filter_by(id=user_id).delete()
                db.session.commit()


@pytest.mark.usefixtures("chat_tables_clean")
class TestAttachmentBytes:
    def test_bytes_that_are_not_the_declared_type_are_refused(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        html = b"<html><script>alert(1)</script></html>"
        for name, ctype in (("fake.png", "image/png"), ("fake.m4a", "audio/mpeg")):
            resp = inv_client.post(
                f"/api/v1/chat/channels/{key}/messages",
                data={"file": (io.BytesIO(html), name, ctype)},
                headers=_auth(member_token),
                content_type="multipart/form-data",
            )
            assert resp.status_code == 415, resp.get_json()

    def test_download_is_never_sniffed(self, inv_client, member_token, invitation_app):
        key = _project_key(invitation_app)
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        sent = inv_client.post(
            f"/api/v1/chat/channels/{key}/messages",
            data={"file": (io.BytesIO(png), "a.png", "image/png")},
            headers=_auth(member_token),
            content_type="multipart/form-data",
        ).get_json()
        resp = inv_client.get(sent["attachment"]["url"], headers=_auth(member_token))
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in resp.headers["Content-Security-Policy"]


@pytest.mark.usefixtures("chat_tables_clean")
class TestProjectChannelLabel:
    def test_project_channel_uses_the_shared_project_label(self, inv_client, member_token, invitation_app):
        """The channel name follows project_label, like the bell, pushes and the apps."""
        from app import db
        from app.application.push.project_label import project_label
        from app.infrastructure.database.models import ProjectModel

        with invitation_app.app_context():
            project = db.session.get(ProjectModel, uuid.UUID(invitation_app._test_project_id))
            project.address = "8 avenue Foch, 94110 Arcueil"
            db.session.commit()
            expected = project_label(project)
        try:
            channel = _channels(inv_client, member_token)[_project_key(invitation_app)]
            assert channel["name"] == expected
            page = inv_client.get(
                f"/api/v1/chat/channels/{_project_key(invitation_app)}/messages", headers=_auth(member_token)
            )
            assert page.status_code == 200
        finally:
            with invitation_app.app_context():
                project = db.session.get(ProjectModel, uuid.UUID(invitation_app._test_project_id))
                project.address = None
                db.session.commit()


@pytest.mark.usefixtures("chat_tables_clean")
class TestDeletedSenderState:
    def test_erased_sender_is_flagged_for_the_apps_to_translate(self, inv_client, admin_token, invitation_app):
        from app import db
        from app.infrastructure.database.models import UserModel

        key = _project_key(invitation_app)
        from tests.auth_login_helper import mint_access_token

        target_token = mint_access_token(inv_client, invitation_app._test_target_user_email)
        _send(inv_client, target_token, key, "au revoir")
        with invitation_app.app_context():
            user = db.session.get(UserModel, uuid.UUID(invitation_app._test_target_user_id))
            user.deleted_at = datetime.now(timezone.utc)
            db.session.commit()
        try:
            page = inv_client.get(f"/api/v1/chat/channels/{key}/messages", headers=_auth(admin_token)).get_json()
            last = page["items"][-1]
            assert last["sender_state"] == "deleted"
            assert page["items"][0]["sender_state"] in (None, "deleted")
        finally:
            with invitation_app.app_context():
                user = db.session.get(UserModel, uuid.UUID(invitation_app._test_target_user_id))
                user.deleted_at = None
                db.session.commit()
