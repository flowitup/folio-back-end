"""API integration tests: DELETE /auth/me — self-service account deletion.

Required by App Store guideline 5.1.1(v). The behaviour under test is deliberately
narrow: the person disappears, their company's records do not, and the session ends
everywhere rather than only on the device that asked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

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

    def test_a_token_minted_before_deletion_on_another_device_is_rejected(
        self, inv_client, invitation_app
    ):
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
        refreshed = inv_client.post(
            "/api/v1/auth/refresh", headers=_auth(other_device["refresh_token"])
        )
        assert refreshed.status_code == 401

    def test_the_phone_number_is_released_for_a_fresh_signup(self, inv_client, invitation_app):
        """D3: the phone is the sign-in identity and is unique — keeping it would
        both retain personal data and lock the person out of ever signing up again."""
        _make_user(invitation_app, "reusable-phone@example.com", "+33600009004")
        token = mint_access_token(inv_client, "reusable-phone@example.com")

        inv_client.delete("/api/v1/auth/me", headers=_auth(token))

        response = inv_client.post(
            "/api/v1/auth/signup/request", json={"phone": "+33600009004"}
        )
        assert response.status_code == 202, response.get_json()

    def test_deleting_twice_is_not_found_rather_than_a_500(self, inv_client, invitation_app):
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
            remaining = (
                db.session.query(UserCompanyAccessModel).filter_by(user_id=user.id).count()
            )
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
        assert body["reason"] == "last_company_admin"
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
