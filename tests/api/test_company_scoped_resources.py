"""API integration tests: two-company scoping (Phase 2 slice A).

Covers the three company-scoped surfaces this slice adds:
  - GET/POST /labor/roles — role name reuse across companies allowed;
    company A's roles never appear in company B's list.
  - GET/POST /billing-document-templates — `?company_id=` requires company
    admin; a company's templates never appear in another company's list.
  - GET /persons — admin/manager-scoped search; a person profiled only in
    company B never appears in company A's search results; a member-only
    caller gets 403.

Same fixture-app pattern as tests/api/test_projects_tenancy.py: two
companies, one admin (+ one member) each, wired manually since
`configure_container()` replaces the container built by `create_app()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def scoped_app():
    """Two companies, one admin (+ one member) each, wired for labor roles,
    billing templates, and persons search."""
    from app import create_app, db
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
    from app.infrastructure.database.repositories.sqlalchemy_billing_template_repository import (
        SqlAlchemyBillingTemplateRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
        SqlAlchemyCompanyRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_person_repository import (
        SqlAlchemyPersonRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from app.application.billing import CreateTemplateUseCase, ListTemplatesUseCase
    from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase
    from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase
    from app.application.persons import SearchPersonsUseCase
    from app.api.v1.authz_context import get_reader_cache
    from config import TestingConfig
    from wiring import configure_container, get_container

    class ScopedTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(ScopedTestConfig)

    with test_app.app_context():
        db.create_all()
        now = datetime.now(timezone.utc)

        def user(email: str) -> UserModel:
            u = UserModel(id=uuid4(), email=email, is_active=True)
            db.session.add(u)
            return u

        admin_a = user("sc_admin_a@test.com")
        member_a = user("sc_member_a@test.com")
        admin_b = user("sc_admin_b@test.com")
        db.session.flush()

        company_a = CompanyModel(
            id=uuid4(),
            legal_name="Scoped Co A",
            address="1 rue A",
            created_by=admin_a.id,
            created_at=now,
            updated_at=now,
        )
        company_b = CompanyModel(
            id=uuid4(),
            legal_name="Scoped Co B",
            address="2 rue B",
            created_by=admin_b.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_a, company_b])
        db.session.flush()

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=admin_a.id, company_id=company_a.id, role="admin", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=member_a.id, company_id=company_a.id, role="member", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=admin_b.id, company_id=company_b.id, role="admin", is_primary=True, attached_at=now
                ),
            ]
        )

        # A person visible only via company_b's directory.
        person_b_only = PersonModel(
            id=uuid4(),
            name="Only In B",
            normalized_name="only in b",
            created_by_user_id=admin_b.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(person_b_only)
        db.session.flush()
        db.session.add(
            CompanyPersonModel(
                id=uuid4(), company_id=company_b.id, person_id=person_b_only.id, is_active=True, created_at=now
            )
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
        _c.authz_reader = SqlAlchemyAuthzReader(db.session, cache_provider=get_reader_cache)

        labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
        _c.labor_role_repository = labor_role_repo
        _c.create_labor_role_usecase = CreateLaborRoleUseCase(repo=labor_role_repo, db_session=db.session)
        _c.list_labor_roles_usecase = ListLaborRolesUseCase(repo=labor_role_repo)

        billing_template_repo = SqlAlchemyBillingTemplateRepository(db.session)
        _c.billing_template_repo = billing_template_repo
        _c.create_billing_template_usecase = CreateTemplateUseCase(template_repo=billing_template_repo)
        _c.list_billing_templates_usecase = ListTemplatesUseCase(template_repo=billing_template_repo)

        person_repo = SqlAlchemyPersonRepository(db.session)
        _c.person_repo = person_repo
        _c.search_persons_usecase = SearchPersonsUseCase(person_repo=person_repo)

        test_app._admin_a_id = admin_a.id
        test_app._member_a_id = member_a.id
        test_app._admin_b_id = admin_b.id
        test_app._company_a_id = company_a.id
        test_app._company_b_id = company_b.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(scoped_app):
    return scoped_app.test_client()


def _login(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


@pytest.fixture
def admin_a_h(client):
    return _login(client, "sc_admin_a@test.com")


@pytest.fixture
def member_a_h(client):
    return _login(client, "sc_member_a@test.com")


@pytest.fixture
def admin_b_h(client):
    return _login(client, "sc_admin_b@test.com")


# ---------------------------------------------------------------------------
# Labor roles
# ---------------------------------------------------------------------------


class TestLaborRolesCompanyScope:
    def test_role_name_reuse_across_companies_allowed(self, client, admin_a_h, admin_b_h):
        resp_a = client.post("/api/v1/labor/roles", json={"name": "Chef", "color": "#111111"}, headers=admin_a_h)
        assert resp_a.status_code == 201, resp_a.get_data(as_text=True)

        # Same name, company B — must NOT 409 (per-company uniqueness, not global).
        resp_b = client.post("/api/v1/labor/roles", json={"name": "Chef", "color": "#222222"}, headers=admin_b_h)
        assert resp_b.status_code == 201, resp_b.get_data(as_text=True)

    def test_company_a_roles_do_not_leak_to_company_b_list(self, client, admin_a_h, admin_b_h):
        client.post("/api/v1/labor/roles", json={"name": "Only A Role", "color": "#333333"}, headers=admin_a_h)

        resp_a = client.get("/api/v1/labor/roles", headers=admin_a_h)
        names_a = [r["name"] for r in resp_a.get_json()["roles"]]
        assert "Only A Role" in names_a

        resp_b = client.get("/api/v1/labor/roles", headers=admin_b_h)
        names_b = [r["name"] for r in resp_b.get_json()["roles"]]
        assert "Only A Role" not in names_b

    def test_query_company_id_requires_membership(self, client, admin_a_h, admin_b_h, scoped_app):
        resp = client.get(
            f"/api/v1/labor/roles?company_id={scoped_app._company_a_id}",
            headers=admin_b_h,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Billing templates
# ---------------------------------------------------------------------------


_TPL_BODY = {
    "kind": "devis",
    "name": "Scoped Template",
    "items": [{"description": "X", "quantity": "1", "unit_price": "10", "vat_rate": "20"}],
}


class TestBillingTemplatesCompanyScope:
    def test_create_defaults_to_caller_admin_company(self, client, admin_a_h, scoped_app):
        resp = client.post("/api/v1/billing-document-templates", json=_TPL_BODY, headers=admin_a_h)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["company_id"] == str(scoped_app._company_a_id)

    def test_create_with_explicit_foreign_company_id_forbidden(self, client, admin_b_h, scoped_app):
        body = {**_TPL_BODY, "name": "Cross Company Tpl", "company_id": str(scoped_app._company_a_id)}
        resp = client.post("/api/v1/billing-document-templates", json=body, headers=admin_b_h)
        assert resp.status_code == 403

    def test_list_company_a_does_not_include_company_b_templates(self, client, admin_a_h, admin_b_h, scoped_app):
        client.post(
            "/api/v1/billing-document-templates",
            json={**_TPL_BODY, "name": "B Only Template"},
            headers=admin_b_h,
        )
        resp = client.get(
            f"/api/v1/billing-document-templates?company_id={scoped_app._company_a_id}",
            headers=admin_a_h,
        )
        assert resp.status_code == 200
        names = [t["name"] for t in resp.get_json()["items"]]
        assert "B Only Template" not in names

    def test_list_foreign_company_id_forbidden(self, client, admin_b_h, scoped_app):
        resp = client.get(
            f"/api/v1/billing-document-templates?company_id={scoped_app._company_a_id}",
            headers=admin_b_h,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Persons search
# ---------------------------------------------------------------------------


class TestPersonsSearchCompanyScope:
    def test_admin_a_never_sees_company_b_only_person(self, client, admin_a_h):
        resp = client.get("/api/v1/persons?q=only", headers=admin_a_h)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.get_json()["persons"]]
        assert "Only In B" not in names

    def test_admin_b_sees_their_own_person(self, client, admin_b_h):
        resp = client.get("/api/v1/persons?q=only", headers=admin_b_h)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.get_json()["persons"]]
        assert "Only In B" in names

    def test_member_only_caller_gets_403(self, client, member_a_h):
        resp = client.get("/api/v1/persons?q=only", headers=member_a_h)
        assert resp.status_code == 403
