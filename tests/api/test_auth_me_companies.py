"""API integration tests: `companies` field on /auth/me and POST /auth/otp/verify.

Phase 1 (roles & permissions redesign): `/auth/me` gains a `companies` array
built from `CompanyRepositoryPort.list_attached_for_user` — `{id, legal_name,
role, is_primary}` per attached company. Existing fields (`permissions`,
`roles`, `phone`) are untouched. `POST /auth/otp/verify` (and the sign-up/
invitation-accept responses, sharing `_login_response`) return the exact same
`companies` shape so clients don't need a follow-up `/auth/me` call to know
which companies the user belongs to.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

MULTI_PHONE = "+33600000201"
NONE_PHONE = "+33600000202"


def _code_from_sms(app) -> str:
    to, text = app._sms.sent[-1]
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match.group(1)


@pytest.fixture(scope="module")
def me_app():
    """Flask app wired with in-memory SQLite: one user in two companies, one user in none."""
    from app import create_app, db
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
    from config import TestingConfig
    from wiring import configure_container, get_container

    class MeTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(MeTestConfig)

    with test_app.app_context():
        db.create_all()
        now = datetime.now(timezone.utc)

        multi = UserModel(email="me_multi@test.com", is_active=True, phone=MULTI_PHONE)
        none_user = UserModel(email="me_none@test.com", is_active=True, phone=NONE_PHONE)
        db.session.add_all([multi, none_user])
        db.session.flush()

        company_a = CompanyModel(
            id=uuid4(), legal_name="Me Co A", address="1 rue A", created_by=multi.id, created_at=now, updated_at=now
        )
        company_b = CompanyModel(
            id=uuid4(), legal_name="Me Co B", address="2 rue B", created_by=multi.id, created_at=now, updated_at=now
        )
        db.session.add_all([company_a, company_b])
        db.session.flush()

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=multi.id, company_id=company_a.id, role="admin", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=multi.id, company_id=company_b.id, role="member", is_primary=False, attached_at=now
                ),
            ]
        )
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        company_repo = SqlAlchemyCompanyRepository(db.session)
        access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
        )

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo
        _c.authz_reader = SqlAlchemyAuthzReader(db.session)

        # Phone sign-in so the two POST /auth/otp/verify tests below can prove the
        # companies[] shape without a real SMS provider.
        from app.application.usecases.otp_login import RequestOtpUseCase, VerifyOtpUseCase
        from app.infrastructure.adapters.sqlalchemy_login_otp import SQLAlchemyLoginOtpRepository

        class _RecordingSmsSender:
            def __init__(self) -> None:
                self.sent: list[tuple[str, str]] = []

            def send(self, to: str, text: str) -> None:
                self.sent.append((to, text))

        otp_repo = SQLAlchemyLoginOtpRepository(db.session)
        sms = _RecordingSmsSender()
        _c.sms_sender = sms
        _c.login_otp_repository = otp_repo
        _c.request_otp_usecase = RequestOtpUseCase(user_repo, otp_repo, sms)
        _c.verify_otp_usecase = VerifyOtpUseCase(user_repo, otp_repo, _c.authorization_service, _c.token_issuer)
        test_app._sms = sms

        test_app._company_a_id = company_a.id
        test_app._company_b_id = company_b.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(me_app):
    return me_app.test_client()


def _login(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


def test_me_lists_all_attached_companies_with_role_and_primary_flag(client, me_app):
    h = _login(client, "me_multi@test.com")
    resp = client.get("/api/v1/auth/me", headers=h)
    assert resp.status_code == 200
    body = resp.get_json()
    by_id = {c["id"]: c for c in body["companies"]}
    assert set(by_id.keys()) == {str(me_app._company_a_id), str(me_app._company_b_id)}
    assert by_id[str(me_app._company_a_id)]["role"] == "admin"
    assert by_id[str(me_app._company_a_id)]["is_primary"] is True
    assert by_id[str(me_app._company_a_id)]["legal_name"] == "Me Co A"
    assert by_id[str(me_app._company_b_id)]["role"] == "member"
    assert by_id[str(me_app._company_b_id)]["is_primary"] is False


def test_me_companies_empty_for_user_with_no_company(client):
    h = _login(client, "me_none@test.com")
    resp = client.get("/api/v1/auth/me", headers=h)
    assert resp.status_code == 200
    assert resp.get_json()["companies"] == []


def test_me_existing_fields_unchanged(client):
    h = _login(client, "me_none@test.com")
    resp = client.get("/api/v1/auth/me", headers=h)
    body = resp.get_json()
    assert "permissions" in body and "phone" in body
    assert "roles" not in body  # a user carries no roles of their own


def test_otp_verify_response_includes_companies_matching_me(client, me_app):
    """POST /auth/otp/verify returns the same companies[] shape as GET /auth/me, no extra call needed."""
    assert client.post("/api/v1/auth/otp/request", json={"phone": MULTI_PHONE}).status_code == 202
    resp = client.post("/api/v1/auth/otp/verify", json={"phone": MULTI_PHONE, "code": _code_from_sms(me_app)})
    assert resp.status_code == 200
    body = resp.get_json()["user"]
    by_id = {c["id"]: c for c in body["companies"]}
    assert set(by_id.keys()) == {str(me_app._company_a_id), str(me_app._company_b_id)}
    assert by_id[str(me_app._company_a_id)]["role"] == "admin"
    assert by_id[str(me_app._company_a_id)]["is_primary"] is True
    assert by_id[str(me_app._company_b_id)]["role"] == "member"
    assert by_id[str(me_app._company_b_id)]["is_primary"] is False


def test_otp_verify_response_companies_empty_for_user_with_no_company(client, me_app):
    assert client.post("/api/v1/auth/otp/request", json={"phone": NONE_PHONE}).status_code == 202
    resp = client.post("/api/v1/auth/otp/verify", json={"phone": NONE_PHONE, "code": _code_from_sms(me_app)})
    assert resp.status_code == 200
    assert resp.get_json()["user"]["companies"] == []
