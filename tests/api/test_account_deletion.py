"""API integration tests: DELETE /auth/me — self-service account deletion.

Required by App Store guideline 5.1.1(v). The behaviour under test is deliberately
narrow: the person disappears, their company's records do not, and the session ends
everywhere rather than only on the device that asked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token, mint_tokens


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user(app, email: str, phone: str | None = None) -> UserModel:
    """Insert an active user directly; returns the persisted ORM row."""
    from app import db

    with app.app_context():
        user = UserModel(
            id=uuid4(),
            email=email,
            is_active=True,
            display_name="Jean Dupont",
            phone=phone,
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        db.session.expunge(user)
        return user


def _make_company(app, created_by, legal_name: str = "Entreprise Test") -> CompanyModel:
    from app import db

    with app.app_context():
        company = CompanyModel(
            id=uuid4(),
            legal_name=legal_name,
            address="1 rue de Test, 75001 Paris",
            created_by=created_by,
        )
        db.session.add(company)
        db.session.commit()
        db.session.refresh(company)
        db.session.expunge(company)
        return company


def _attach(app, user_id, company_id, role: str) -> None:
    from app import db

    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id,
                company_id=company_id,
                is_primary=False,
                role=role,
                attached_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()


def _reload(app, user_id) -> UserModel | None:
    from app import db

    with app.app_context():
        return db.session.get(UserModel, user_id)


class TestDeleteAccountHappyPath:
    def test_erases_every_personal_field_and_returns_204(self, inv_client, invitation_app):
        user = _make_user(invitation_app, "solo-delete@example.com", "+33600009001")
        token = mint_access_token(inv_client, "solo-delete@example.com")

        response = inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        assert response.status_code == 204
        row = _reload(invitation_app, user.id)
        assert row is not None, "the row must survive — company data references it"
        assert row.email == f"deleted-{user.id}@deleted.invalid"
        assert row.phone is None
        assert row.display_name is None
        assert row.is_active is False
        assert row.deleted_at is not None

    def test_the_caller_token_stops_working_immediately(self, inv_client, invitation_app):
        _make_user(invitation_app, "token-dead@example.com", "+33600009002")
        token = mint_access_token(inv_client, "token-dead@example.com")

        assert inv_client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 200
        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204
        assert inv_client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401

    def test_a_token_minted_before_deletion_on_another_device_is_rejected(self, inv_client, invitation_app):
        """The reason the user check lives in the blocklist loader (D1).

        Revocation is per-JTI, so a second device's token is never presented at
        deletion time. Before this, it kept working — mobile refresh tokens never
        expire — which would have made "delete my account" a lie.
        """
        _make_user(invitation_app, "second-device@example.com", "+33600009003")
        other_device = mint_tokens(inv_client, "second-device@example.com")
        deleting_device = mint_access_token(inv_client, "second-device@example.com")

        inv_client.delete("/api/v1/auth/me", headers=_auth(deleting_device))

        assert inv_client.get("/api/v1/auth/me", headers=_auth(other_device["access_token"])).status_code == 401
        refreshed = inv_client.post("/api/v1/auth/refresh", headers=_auth(other_device["refresh_token"]))
        assert refreshed.status_code == 401

    def test_the_phone_number_is_released_for_a_fresh_signup(self, inv_client, invitation_app):
        """D3: the phone is the sign-in identity and is unique — keeping it would
        both retain personal data and lock the person out of ever signing up again."""
        _make_user(invitation_app, "reusable-phone@example.com", "+33600009004")
        token = mint_access_token(inv_client, "reusable-phone@example.com")

        inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        response = inv_client.post("/api/v1/auth/signup/request", json={"phone": "+33600009004"})
        assert response.status_code == 202, response.get_json()

    def test_a_second_token_for_the_same_account_is_dead_too(self, inv_client, invitation_app):
        _make_user(invitation_app, "twice@example.com", "+33600009005")
        token = mint_access_token(inv_client, "twice@example.com")
        second_token = mint_access_token(inv_client, "twice@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204
        assert inv_client.delete("/api/v1/auth/me", headers=_auth(second_token)).status_code == 401


class TestCompanyDataSurvives:
    def test_the_company_the_user_created_is_untouched(self, inv_client, invitation_app):
        from app import db

        user = _make_user(invitation_app, "founder@example.com", "+33600009010")
        company = _make_company(invitation_app, user.id, "Bâtiment Dupont")
        _attach(invitation_app, user.id, company.id, "admin")
        token = mint_access_token(inv_client, "founder@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

        with invitation_app.app_context():
            survivor = db.session.get(CompanyModel, company.id)
            assert survivor is not None
            assert survivor.legal_name == "Bâtiment Dupont"
            assert survivor.created_by == user.id, "the audit trail still points at the anonymous row"

    def test_company_access_rows_are_removed(self, inv_client, invitation_app):
        from app import db

        user = _make_user(invitation_app, "detached@example.com", "+33600009011")
        company = _make_company(invitation_app, user.id)
        _attach(invitation_app, user.id, company.id, "admin")
        token = mint_access_token(inv_client, "detached@example.com")

        inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        with invitation_app.app_context():
            remaining = db.session.query(UserCompanyAccessModel).filter_by(user_id=user.id).count()
            assert remaining == 0


class TestLastAdminGuard:
    def test_last_admin_of_a_shared_company_is_refused(self, inv_client, invitation_app):
        """D2: erasing the only admin would strand the remaining members."""
        admin = _make_user(invitation_app, "last-admin@example.com", "+33600009020")
        colleague = _make_user(invitation_app, "colleague@example.com", "+33600009021")
        company = _make_company(invitation_app, admin.id, "Maçonnerie Martin")
        _attach(invitation_app, admin.id, company.id, "admin")
        _attach(invitation_app, colleague.id, company.id, "member")
        token = mint_access_token(inv_client, "last-admin@example.com")

        response = inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        assert response.status_code == 409
        body = response.get_json()
        # Same discriminator the company demote/boot/detach endpoints emit for the
        # same condition, so clients need one branch rather than two.
        assert body["reason"] == "last_admin"
        assert body["company_name"] == "Maçonnerie Martin"

    def test_a_refused_deletion_writes_nothing(self, inv_client, invitation_app):
        admin = _make_user(invitation_app, "intact@example.com", "+33600009022")
        colleague = _make_user(invitation_app, "intact-colleague@example.com", "+33600009023")
        company = _make_company(invitation_app, admin.id)
        _attach(invitation_app, admin.id, company.id, "admin")
        _attach(invitation_app, colleague.id, company.id, "member")
        token = mint_access_token(inv_client, "intact@example.com")

        inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        row = _reload(invitation_app, admin.id)
        assert row.email == "intact@example.com"
        assert row.phone == "+33600009022"
        assert row.is_active is True
        assert row.deleted_at is None
        assert inv_client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 200

    def test_a_sole_member_admin_may_still_delete(self, inv_client, invitation_app):
        """Nobody is stranded, so the guard must not fire — otherwise a user who
        created their own company could never delete their account, which is
        exactly what the guideline forbids."""
        user = _make_user(invitation_app, "solo-admin@example.com", "+33600009024")
        company = _make_company(invitation_app, user.id)
        _attach(invitation_app, user.id, company.id, "admin")
        token = mint_access_token(inv_client, "solo-admin@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

    def test_a_second_admin_lets_the_first_one_leave(self, inv_client, invitation_app):
        leaving = _make_user(invitation_app, "leaving-admin@example.com", "+33600009025")
        staying = _make_user(invitation_app, "staying-admin@example.com", "+33600009026")
        company = _make_company(invitation_app, leaving.id)
        _attach(invitation_app, leaving.id, company.id, "admin")
        _attach(invitation_app, staying.id, company.id, "admin")
        token = mint_access_token(inv_client, "leaving-admin@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

    def test_a_member_is_never_blocked(self, inv_client, invitation_app):
        admin = _make_user(invitation_app, "the-admin@example.com", "+33600009027")
        member = _make_user(invitation_app, "just-a-member@example.com", "+33600009028")
        company = _make_company(invitation_app, admin.id)
        _attach(invitation_app, admin.id, company.id, "admin")
        _attach(invitation_app, member.id, company.id, "member")
        token = mint_access_token(inv_client, "just-a-member@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204


class TestAuthentication:
    def test_anonymous_callers_are_rejected(self, inv_client):
        assert inv_client.delete("/api/v1/auth/me").status_code == 401

    @pytest.mark.parametrize("token", ["", "not-a-jwt"])
    def test_garbage_tokens_are_rejected(self, inv_client, token):
        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code in (401, 422)


class TestWhatSurvivesErasure:
    """The erase/keep split is the whole design, so it is asserted directly.

    `billing_documents.created_by` and `chat_messages.sender_id` are CASCADE to
    `users.id` — a row delete would take them with it. Adding either table to the
    eraser would destroy company records, and nothing else in the suite would notice.
    """

    def test_company_records_and_chat_messages_survive(self, inv_client, invitation_app):
        from app import db
        from app.infrastructure.database.models.billing_document import BillingDocumentModel
        from app.infrastructure.database.models.chat_message import ChatMessageOrm

        user = _make_user(invitation_app, "keeps-records@example.com", "+33600009030")
        company = _make_company(invitation_app, user.id)
        _attach(invitation_app, user.id, company.id, "admin")
        token = mint_access_token(inv_client, "keeps-records@example.com")

        with invitation_app.app_context():
            message = ChatMessageOrm(
                id=uuid4(),
                channel_kind="project",
                channel_id=uuid4(),
                sender_id=user.id,
                body="Béton livré ce matin",
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(message)
            db.session.commit()
            message_id = message.id
            billing_before = db.session.query(BillingDocumentModel).count()

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

        with invitation_app.app_context():
            survivor = db.session.get(ChatMessageOrm, message_id)
            assert survivor is not None, "chat history must survive an account erasure"
            assert survivor.body == "Béton livré ce matin"
            assert survivor.sender_id == user.id
            assert db.session.query(BillingDocumentModel).count() == billing_before

    def test_credentials_devices_and_grants_are_gone(self, inv_client, invitation_app):
        from app import db
        from app.infrastructure.database.models.api_key import ApiKeyOrm
        from app.infrastructure.database.models.notification_preference import (
            NotificationPreferenceModel,
        )
        from app.infrastructure.database.models.push_device import PushDeviceOrm

        user = _make_user(invitation_app, "has-credentials@example.com", "+33600009031")
        token = mint_access_token(inv_client, "has-credentials@example.com")

        with invitation_app.app_context():
            db.session.add(
                ApiKeyOrm(
                    id=uuid4(),
                    user_id=user.id,
                    name="ci",
                    prefix="folio_ci",
                    token_hash=uuid4().hex + uuid4().hex,
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.session.add(
                PushDeviceOrm(
                    id=uuid4(),
                    user_id=user.id,
                    token="ExponentPushToken[test]",
                    platform="ios",
                    created_at=datetime.now(timezone.utc),
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            db.session.add(NotificationPreferenceModel(user_id=user.id, updated_at=datetime.now(timezone.utc)))
            db.session.commit()

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

        with invitation_app.app_context():
            assert db.session.query(ApiKeyOrm).filter_by(user_id=user.id).count() == 0
            assert db.session.query(PushDeviceOrm).filter_by(user_id=user.id).count() == 0
            assert db.session.query(NotificationPreferenceModel).filter_by(user_id=user.id).count() == 0

    def test_the_platform_ops_bypass_is_cleared(self, inv_client, invitation_app):
        """An erased support account must not come back carrying its bypass."""
        from app import db

        user = _make_user(invitation_app, "ops-leaving@example.com", "+33600009032")
        with invitation_app.app_context():
            row = db.session.get(UserModel, user.id)
            row.is_platform_ops = True
            db.session.commit()
        token = mint_access_token(inv_client, "ops-leaving@example.com")

        assert inv_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204

        assert _reload(invitation_app, user.id).is_platform_ops is False

    def test_the_account_is_not_re_identifiable_through_its_worker_record(self, inv_client, invitation_app):
        """The worker stays (the company needs it); the link back to the person goes."""
        from app import db
        from app.infrastructure.database.models.worker import WorkerModel

        user = _make_user(invitation_app, "on-site@example.com", "+33600009033")
        token = mint_access_token(inv_client, "on-site@example.com")
        with invitation_app.app_context():
            worker = WorkerModel(
                id=uuid4(),
                project_id=UUID(invitation_app._test_project_2_id),
                name="Jean Dupont",
                daily_rate=Decimal("180.00"),
                user_id=user.id,
            )
            db.session.add(worker)
            db.session.commit()
            worker_id = worker.id

        inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        with invitation_app.app_context():
            kept = db.session.get(WorkerModel, worker_id)
            assert kept is not None and kept.name == "Jean Dupont"
            assert kept.user_id is None, "the erased account must not stay linkable"


class TestRequestHandling:
    def test_a_malformed_body_does_not_break_the_erasure(self, inv_client, invitation_app):
        """A body that is valid JSON but not an object used to raise past the commit,
        leaving the account erased while the response said 500 and the cookies stayed."""
        user = _make_user(invitation_app, "odd-body@example.com", "+33600009040")
        token = mint_access_token(inv_client, "odd-body@example.com")

        response = inv_client.delete(
            "/api/v1/auth/me",
            headers={**_auth(token), "Content-Type": "application/json"},
            data='["not-an-object"]',
        )

        assert response.status_code == 204
        assert _reload(invitation_app, user.id).deleted_at is not None

    def test_a_deactivated_account_can_still_sign_out(self, inv_client, invitation_app):
        """Logout grants no access, so the per-request "may this user sign in?"
        check must not reach it: a 401 there never runs unset_jwt_cookies, leaving
        the client holding the credentials it asked to discard. (A token revoked
        by JTI still 401s — that is the blocklist doing its job, not this check.)"""
        from app import db

        user = _make_user(invitation_app, "deactivated@example.com", "+33600009041")
        token = mint_access_token(inv_client, "deactivated@example.com")
        with invitation_app.app_context():
            db.session.get(UserModel, user.id).is_active = False
            db.session.commit()

        assert inv_client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401
        assert inv_client.post("/api/v1/auth/logout", headers=_auth(token)).status_code == 200
