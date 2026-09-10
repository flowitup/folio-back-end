"""API tests for creating a worker FROM a company person profile (Phase 2 onboarding slice B):

  POST /projects/<id>/workers {person_id}

fills name/phone from `persons`, `daily_rate` from `company_persons.default_daily_rate`
when the body omits it, `user_id` from `persons.user_id`; 409 if that user already
has a worker on the project.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def wfp_app():
    from app import create_app, db
    from config import TestingConfig

    class WfpTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(WfpTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def wfp_client(wfp_app):
    return wfp_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


def _make_user(app, email: str) -> str:
    from app import db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, password_hash=Argon2PasswordHasher().hash(PASSWORD), is_active=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_company_and_project(app, admin_id):
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(), legal_name="WFP Co", address="1 rue", created_by=admin_id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        project = ProjectModel(id=uuid4(), name="WFP Project", owner_id=admin_id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return company.id, project.id


def _make_company_person(app, company_id, *, name, phone, default_daily_rate=None, user_id=None, labor_role_id=None):
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        person = PersonModel(
            id=uuid4(),
            name=name,
            normalized_name=name.lower(),
            created_by_user_id=user_id or uuid4(),
            created_at=now,
            phone=phone,
            user_id=user_id,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            CompanyPersonModel(
                id=uuid4(),
                company_id=company_id,
                person_id=person.id,
                is_active=True,
                created_at=now,
                default_daily_rate=Decimal(str(default_daily_rate)) if default_daily_rate is not None else None,
                labor_role_id=labor_role_id,
            )
        )
        db.session.commit()
        return person.id


class TestWorkerFromPerson:
    def test_fills_name_phone_and_default_rate_from_profile(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_admin1@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        person_id = _make_company_person(
            wfp_app, company_id, name="Profile Person", phone="+33611119001", default_daily_rate="150.00"
        )
        token = _login(wfp_client, "wfp_admin1@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["name"] == "Profile Person"
        assert body["phone"] == "+33611119001"
        assert body["daily_rate"] == 150.0

    def test_explicit_body_values_win_over_profile_defaults(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_admin2@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        person_id = _make_company_person(
            wfp_app, company_id, name="Profile Person 2", phone="+33611119002", default_daily_rate="150.00"
        )
        token = _login(wfp_client, "wfp_admin2@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id), "daily_rate": 200.0, "name": "Override Name"},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["name"] == "Override Name"
        assert body["daily_rate"] == 200.0

    def test_derives_user_id_from_person_and_409_on_duplicate(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_admin3@test.com")
        linked_user_id = _make_user(wfp_app, "wfp_linked3@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        person_id = _make_company_person(
            wfp_app,
            company_id,
            name="Linked Person",
            phone="+33611119003",
            default_daily_rate="120.00",
            user_id=linked_user_id,
        )
        token = _login(wfp_client, "wfp_admin3@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["user_id"] == str(linked_user_id)

        # Creating a SECOND worker on this project from the same linked
        # user (via person_id) must be rejected 409 — the account already
        # has a worker here.
        resp2 = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp2.status_code == 409

    def test_missing_rate_and_no_profile_default_is_400(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_admin4@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        person_id = _make_company_person(wfp_app, company_id, name="No Rate Person", phone="+33611119005")
        token = _login(wfp_client, "wfp_admin4@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 400


class TestRateSetThroughTheApiIsInherited:
    """Closes the loop the pay-defaults endpoint exists for: a rate typed once
    at company level is what the next project's worker costs, with no rate in
    the create-worker body at all."""

    def test_patched_company_rate_becomes_the_new_workers_rate(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_inherit1@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        # No rate on the profile: exactly the state every person onboarded
        # through POST /companies/<id>/members starts in.
        person_id = _make_company_person(
            wfp_app, company_id, name="Inherit Person", phone="+33611119101", default_daily_rate=None
        )
        token = _login(wfp_client, "wfp_inherit1@test.com")

        without_rate = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert without_rate.status_code == 400, "a profile with no rate cannot price a worker on its own"

        patched = wfp_client.patch(
            f"/api/v1/companies/{company_id}/members/{person_id}",
            json={"default_daily_rate": 175.5},
            headers=_auth(token),
        )
        assert patched.status_code == 200, patched.get_data(as_text=True)

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["daily_rate"] == 175.5


def _make_labor_role(app, company_id, *, name):
    from app import db
    from app.infrastructure.database.models.labor_role import LaborRoleModel

    with app.app_context():
        role = LaborRoleModel(
            id=uuid4(),
            company_id=company_id,
            name=name,
            color="#654321",
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(role)
        db.session.commit()
        return role.id


class TestLaborRoleFromProfile:
    """The company profile answers `role_id` the same way it answers the rate:
    only when the request left it out."""

    def test_worker_inherits_the_company_labor_role(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_role1@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        role_id = _make_labor_role(wfp_app, company_id, name="Thợ chính")
        person_id = _make_company_person(
            wfp_app,
            company_id,
            name="Role Person",
            phone="+33611119201",
            default_daily_rate="140.00",
            labor_role_id=role_id,
        )
        token = _login(wfp_client, "wfp_role1@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["role_id"] == str(role_id)

    def test_an_explicit_role_in_the_body_wins(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_role2@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        profile_role = _make_labor_role(wfp_app, company_id, name="Profile Role")
        chosen_role = _make_labor_role(wfp_app, company_id, name="Chosen Role")
        person_id = _make_company_person(
            wfp_app,
            company_id,
            name="Role Person 2",
            phone="+33611119202",
            default_daily_rate="140.00",
            labor_role_id=profile_role,
        )
        token = _login(wfp_client, "wfp_role2@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id), "role_id": str(chosen_role)},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["role_id"] == str(chosen_role)

    def test_no_role_on_the_profile_leaves_the_worker_without_one(self, wfp_client, wfp_app):
        admin_id = _make_user(wfp_app, "wfp_role3@test.com")
        company_id, project_id = _make_company_and_project(wfp_app, admin_id)
        person_id = _make_company_person(
            wfp_app, company_id, name="Role Person 3", phone="+33611119203", default_daily_rate="140.00"
        )
        token = _login(wfp_client, "wfp_role3@test.com")

        resp = wfp_client.post(
            f"/api/v1/projects/{project_id}/workers",
            json={"person_id": str(person_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["role_id"] is None
