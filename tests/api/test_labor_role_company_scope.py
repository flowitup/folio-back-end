"""Labor roles for users of several companies (QA round 3, owner bug "new company + labor role").

- A user added to two companies by their admins has no primary row: GET /labor/roles with no
  company used to return the company-less legacy rows that no project accepts. It now answers the
  user's default company (primary, else oldest attachment), and never the legacy rows.
- ``?company_id=`` / ``?project_id=`` pick the company explicitly; POST with ``?company_id=``
  creates the role in that company.
- A name a client would display like a default role ("Ouvrier principal" for the role stored as
  "Thợ chính") is a duplicate.
- A worker saved with another company's role gets the dedicated ``InvalidLaborRole`` error.
- GET /persons?company_id= keeps only that company's directory.
- Default roles are seeded in the creator's language.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import ProjectModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.labor_role import LaborRoleModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def scope_app():
    from app import create_app, db
    from app.api.v1.authz_context import get_reader_cache
    from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase
    from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase
    from app.application.persons import SearchPersonsUseCase
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
    from app.infrastructure.database.repositories.sqlalchemy_person_repository import SqlAlchemyPersonRepository
    from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
        SqlAlchemyUserCompanyAccessRepository,
    )
    from config import TestingConfig
    from wiring import configure_container, get_container

    class ScopeTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(ScopeTestConfig)
    with test_app.app_context():
        db.create_all()
        now = datetime.now(timezone.utc)

        manager = UserModel(id=uuid4(), email="ls_manager@test.com", is_active=True)
        loner = UserModel(id=uuid4(), email="ls_loner@test.com", is_active=True)
        db.session.add_all([manager, loner])
        db.session.flush()

        def company(name: str) -> CompanyModel:
            c = CompanyModel(
                id=uuid4(), legal_name=name, address="1 rue", created_by=manager.id, created_at=now, updated_at=now
            )
            db.session.add(c)
            return c

        company_a, company_b = company("LS Co A"), company("LS Co B")
        db.session.flush()
        # Attached by admins: never primary. A first, B a day later.
        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=manager.id,
                    company_id=company_a.id,
                    role="manager",
                    is_primary=False,
                    attached_at=now - timedelta(days=1),
                ),
                UserCompanyAccessModel(
                    user_id=manager.id, company_id=company_b.id, role="manager", is_primary=False, attached_at=now
                ),
            ]
        )

        def role(name: str, company_id, slug=None) -> LaborRoleModel:
            r = LaborRoleModel(id=uuid4(), company_id=company_id, name=name, slug=slug, color="#3B82F6")
            db.session.add(r)
            return r

        legacy = role("Thợ chính", None, "tho_chinh")
        role_a = role("Thợ chính", company_a.id, "tho_chinh")
        role_b = role("Ouvrier principal", company_b.id, "tho_chinh")
        project_b = ProjectModel(name="LS Project B", owner_id=manager.id, company_id=company_b.id)
        db.session.add(project_b)

        def person(name: str, company_id) -> PersonModel:
            p = PersonModel(
                id=uuid4(),
                name=name,
                normalized_name=name.lower(),
                created_by_user_id=manager.id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(p)
            db.session.flush()
            db.session.add(
                CompanyPersonModel(id=uuid4(), company_id=company_id, person_id=p.id, is_active=True, created_at=now)
            )
            return p

        person("Lsperson Alpha", company_a.id)
        person("Lsperson Beta", company_b.id)
        db.session.commit()

        configure_container(
            user_repository=SQLAlchemyUserRepository(db.session),
            project_repository=SQLAlchemyProjectRepository(db.session),
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
        )
        c = get_container()
        c.user_company_access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        c.authz_reader = SqlAlchemyAuthzReader(db.session, cache_provider=get_reader_cache)
        labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
        c.labor_role_repository = labor_role_repo
        c.create_labor_role_usecase = CreateLaborRoleUseCase(repo=labor_role_repo, db_session=db.session)
        c.list_labor_roles_usecase = ListLaborRolesUseCase(repo=labor_role_repo)
        person_repo = SqlAlchemyPersonRepository(db.session)
        c.person_repo = person_repo
        c.search_persons_usecase = SearchPersonsUseCase(person_repo=person_repo)

        test_app._ids = {
            "a": str(company_a.id),
            "b": str(company_b.id),
            "legacy": str(legacy.id),
            "role_a": str(role_a.id),
            "role_b": str(role_b.id),
            "project_b": str(project_b.id),
        }
        db.session.expunge_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(scope_app):
    return scope_app.test_client()


@pytest.fixture
def manager_h(client):
    return {"Authorization": f"Bearer {mint_access_token(client, 'ls_manager@test.com')}"}


@pytest.fixture
def loner_h(client):
    return {"Authorization": f"Bearer {mint_access_token(client, 'ls_loner@test.com')}"}


def _role_ids(resp) -> set[str]:
    assert resp.status_code == 200, resp.get_json()
    return {r["id"] for r in resp.get_json()["roles"]}


class TestListWithoutPrimary:
    def test_no_company_given_lists_the_oldest_attachment_not_legacy_rows(self, client, manager_h, scope_app):
        ids = _role_ids(client.get("/api/v1/labor/roles", headers=manager_h))
        assert scope_app._ids["role_a"] in ids
        assert scope_app._ids["legacy"] not in ids

    def test_company_id_selects_that_company(self, client, manager_h, scope_app):
        ids = _role_ids(client.get(f"/api/v1/labor/roles?company_id={scope_app._ids['b']}", headers=manager_h))
        assert ids == {scope_app._ids["role_b"]}

    def test_project_id_selects_the_projects_company(self, client, manager_h, scope_app):
        ids = _role_ids(client.get(f"/api/v1/labor/roles?project_id={scope_app._ids['project_b']}", headers=manager_h))
        assert ids == {scope_app._ids["role_b"]}

    def test_a_user_with_no_company_gets_an_empty_list(self, client, loner_h, scope_app):
        assert _role_ids(client.get("/api/v1/labor/roles", headers=loner_h)) == set()

    def test_a_user_with_no_company_cannot_create_a_legacy_role(self, client, loner_h):
        resp = client.post("/api/v1/labor/roles", json={"name": "Orphan", "color": "#3B82F6"}, headers=loner_h)
        assert resp.status_code == 400


class TestCreateInTheRequestedCompany:
    def test_manager_creates_in_the_company_named(self, client, manager_h, scope_app):
        b = scope_app._ids["b"]
        resp = client.post(
            f"/api/v1/labor/roles?company_id={b}", json={"name": "LS Carreleur", "color": "#10B981"}, headers=manager_h
        )
        assert resp.status_code == 201, resp.get_json()
        assert resp.get_json()["id"] in _role_ids(client.get(f"/api/v1/labor/roles?company_id={b}", headers=manager_h))

    @pytest.mark.parametrize("name", ["Ouvrier principal", "lead worker", "THỢ CHÍNH"])
    def test_a_name_shown_like_the_default_role_is_a_duplicate(self, client, manager_h, scope_app, name):
        resp = client.post(
            f"/api/v1/labor/roles?company_id={scope_app._ids['a']}",
            json={"name": name, "color": "#10B981"},
            headers=manager_h,
        )
        assert resp.status_code == 409


class TestPersonSearchByCompany:
    def test_company_id_keeps_only_that_directory(self, client, manager_h, scope_app):
        resp = client.get(f"/api/v1/persons?q=lsperson&company_id={scope_app._ids['b']}", headers=manager_h)
        assert resp.status_code == 200
        assert [p["name"] for p in resp.get_json()["persons"]] == ["Lsperson Beta"]

    def test_without_company_id_both_directories(self, client, manager_h):
        resp = client.get("/api/v1/persons?q=lsperson", headers=manager_h)
        assert sorted(p["name"] for p in resp.get_json()["persons"]) == ["Lsperson Alpha", "Lsperson Beta"]

    def test_a_company_outside_the_callers_scope_is_forbidden(self, client, manager_h):
        assert client.get(f"/api/v1/persons?q=lsperson&company_id={uuid4()}", headers=manager_h).status_code == 403


class TestDefaultRoleNames:
    def test_seed_in_french(self):
        from unittest.mock import MagicMock

        from app.application.labor.seed_default_labor_roles import SeedDefaultLaborRolesUseCase

        repo = MagicMock()
        repo.find_by_name.return_value = None
        repo.create.side_effect = lambda role: role
        roles = SeedDefaultLaborRolesUseCase(repo, MagicMock()).execute(company_id=uuid4(), locale="fr-FR")
        assert [(r.name, r.slug) for r in roles] == [
            ("Ouvrier principal", "tho_chinh"),
            ("Ouvrier auxiliaire", "tho_phu"),
        ]

    def test_seed_defaults_to_vietnamese(self):
        from app.application.labor.default_role_names import default_roles_for

        assert [n for n, _c, _s in default_roles_for(None)] == ["Thợ chính", "Thợ phụ"]
        assert [n for n, _c, _s in default_roles_for("de")] == ["Thợ chính", "Thợ phụ"]

    @pytest.mark.parametrize(
        "header,expected",
        [("fr-FR,fr;q=0.9,en;q=0.8", "fr"), ("de-DE,en;q=0.5,vi;q=0.7", "vi"), ("de", None), (None, None)],
    )
    def test_accept_language(self, header, expected):
        from app.application.labor.default_role_names import locale_from_accept_language

        assert locale_from_accept_language(header) == expected
