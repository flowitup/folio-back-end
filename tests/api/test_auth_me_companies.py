"""API integration tests: GET /auth/me `companies` field.

Phase 1 (roles & permissions redesign): `/auth/me` gains a `companies` array
built from `CompanyRepositoryPort.list_attached_for_user` — `{id, legal_name,
role, is_primary}` per attached company. Existing fields (`permissions`,
`roles`, `phone`) are untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def me_app():
    """Flask app wired with in-memory SQLite: one user in two companies, one user in none."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
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
        hasher = Argon2PasswordHasher()
        now = datetime.now(timezone.utc)

        multi = UserModel(email="me_multi@test.com", password_hash=hasher.hash(PASSWORD), is_active=True)
        none_user = UserModel(email="me_none@test.com", password_hash=hasher.hash(PASSWORD), is_active=True)
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
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
        )

        _c = get_container()
        _c.company_repo = company_repo
        _c.user_company_access_repo = access_repo
        _c.authz_reader = SqlAlchemyAuthzReader(db.session)

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
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return {"Authorization": f"Bearer {resp.get_json()['access_token']}"}


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
    assert "permissions" in body and "roles" in body and "phone" in body
