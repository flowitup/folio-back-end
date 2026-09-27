"""Renaming a worker — or changing its phone — changes the shared Person behind it.

PUT /projects/<id>/workers/<wid> {name} updates the linked `persons` row, so the
new name shows on every project and in every company that uses the person —
worker lists, the day roster and the company person search — not only on the
worker row that was edited.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def rename_app():
    from app import create_app, db
    from config import TestingConfig

    class RenameTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(RenameTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(rename_app):
    return rename_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _company_with_admin_and_project(email: str, person_id):
    """A company whose admin has one project, with the person in its directory."""
    from app import db

    now = datetime.now(timezone.utc)
    admin = UserModel(id=uuid4(), email=email, is_active=True)
    db.session.add(admin)
    db.session.flush()
    company = CompanyModel(
        id=uuid4(), legal_name=f"Co {email}", address="1 rue", created_by=admin.id, created_at=now, updated_at=now
    )
    db.session.add(company)
    db.session.flush()
    db.session.add(
        UserCompanyAccessModel(user_id=admin.id, company_id=company.id, role="admin", is_primary=True, attached_at=now)
    )
    project = ProjectModel(id=uuid4(), name=f"Project {email}", owner_id=admin.id, company_id=company.id)
    db.session.add(project)
    db.session.add(
        CompanyPersonModel(
            id=uuid4(),
            company_id=company.id,
            person_id=person_id,
            is_active=True,
            created_at=now,
            default_daily_rate=Decimal("150.00"),
        )
    )
    db.session.commit()
    return project.id


@pytest.fixture
def shared_person(rename_app):
    """One person working for two companies, each with its own project."""
    from app import db

    with rename_app.app_context():
        now = datetime.now(timezone.utc)
        person = PersonModel(
            id=uuid4(),
            name="Jean Dupont",
            normalized_name="jean dupont",
            created_by_user_id=uuid4(),
            created_at=now,
            phone="+33611112222",
        )
        db.session.add(person)
        db.session.commit()
        tag = uuid4().hex[:6]
        project_a = _company_with_admin_and_project(f"rename-a-{tag}@test.com", person.id)
        project_b = _company_with_admin_and_project(f"rename-b-{tag}@test.com", person.id)
        return {
            "person_id": str(person.id),
            "project_a": str(project_a),
            "project_b": str(project_b),
            "admin_a": f"rename-a-{tag}@test.com",
            "admin_b": f"rename-b-{tag}@test.com",
        }


def _add_worker(client, token, project_id, person_id) -> dict:
    resp = client.post(f"/api/v1/projects/{project_id}/workers", json={"person_id": person_id}, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def test_renaming_a_worker_renames_the_person_in_every_company(client, shared_person):
    token_a = mint_access_token(client, shared_person["admin_a"])
    token_b = mint_access_token(client, shared_person["admin_b"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])
    worker_b = _add_worker(client, token_b, shared_person["project_b"], shared_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"name": "Jean Dupont-Martin"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["name"] == "Jean Dupont-Martin"
    assert resp.get_json()["person_name"] == "Jean Dupont-Martin"

    # The other company's project shows the new name too.
    listed = client.get(f"/api/v1/projects/{shared_person['project_b']}/workers", headers=_auth(token_b))
    assert listed.status_code == 200
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_b["id"])
    assert row["person_name"] == "Jean Dupont-Martin"
    assert row["name"] == "Jean Dupont-Martin"

    roster = client.get(
        f"/api/v1/projects/{shared_person['project_b']}/labor/roster",
        query_string={"date": date.today().isoformat()},
        headers=_auth(token_b),
    )
    assert roster.status_code == 200, roster.get_data(as_text=True)
    assert [r["name"] for r in roster.get_json()["rows"] if r["worker_id"] == worker_b["id"]] == ["Jean Dupont-Martin"]

    found = client.get("/api/v1/persons", query_string={"q": "dupont-martin"}, headers=_auth(token_b))
    assert found.status_code == 200
    assert shared_person["person_id"] in [p["id"] for p in found.get_json()["persons"]]


def test_changing_only_the_phone_keeps_the_person_name(client, shared_person, rename_app):
    token_a = mint_access_token(client, shared_person["admin_a"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"phone": "+33699990000"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    from app import db

    with rename_app.app_context():
        assert db.session.get(PersonModel, UUID(shared_person["person_id"])).name == "Jean Dupont"


def test_changing_a_workers_phone_changes_the_person_in_every_company(client, shared_person, rename_app):
    token_a = mint_access_token(client, shared_person["admin_a"])
    token_b = mint_access_token(client, shared_person["admin_b"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])
    worker_b = _add_worker(client, token_b, shared_person["project_b"], shared_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"phone": "06 12 34 56 78"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["phone"] == "06 12 34 56 78"
    assert resp.get_json()["person_phone"] == "06 12 34 56 78"

    # The other company's project shows the new phone too.
    listed = client.get(f"/api/v1/projects/{shared_person['project_b']}/workers", headers=_auth(token_b))
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_b["id"])
    assert row["person_phone"] == "06 12 34 56 78" and row["phone"] == "06 12 34 56 78"

    from app import db

    with rename_app.app_context():
        person = db.session.get(PersonModel, UUID(shared_person["person_id"]))
        assert person.phone == "06 12 34 56 78" and person.phone_normalized == "+33612345678"
        hints = {
            cp.phone_normalized for cp in db.session.query(CompanyPersonModel).filter_by(person_id=person.id).all()
        }
        assert hints == {"+33612345678"}

    # Cleared the same way.
    cleared = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"phone": ""},
        headers=_auth(token_a),
    )
    assert cleared.status_code == 200, cleared.get_data(as_text=True)
    with rename_app.app_context():
        db.session.expire_all()
        assert db.session.get(PersonModel, UUID(shared_person["person_id"])).phone is None


def test_a_number_another_profile_holds_in_a_company_is_not_duplicated_there(client, shared_person, rename_app):
    """The directory allows one active profile per number and company; the person keeps the phone."""
    from app import db

    token_a = mint_access_token(client, shared_person["admin_a"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])
    with rename_app.app_context():
        profile = db.session.query(CompanyPersonModel).filter_by(person_id=UUID(shared_person["person_id"])).first()
        other = PersonModel(
            id=uuid4(),
            name="Other",
            normalized_name="other",
            created_by_user_id=uuid4(),
            created_at=datetime.now(timezone.utc),
            phone="+33687654321",
        )
        db.session.add(other)
        db.session.add(
            CompanyPersonModel(
                id=uuid4(),
                company_id=profile.company_id,
                person_id=other.id,
                is_active=True,
                created_at=datetime.now(timezone.utc),
                phone_normalized="+33687654321",
            )
        )
        db.session.commit()
        company_id = profile.company_id

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"phone": "+33687654321"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with rename_app.app_context():
        db.session.expire_all()
        assert db.session.get(PersonModel, UUID(shared_person["person_id"])).phone == "+33687654321"
        mine = (
            db.session.query(CompanyPersonModel)
            .filter_by(person_id=UUID(shared_person["person_id"]), company_id=company_id)
            .one()
        )
        assert mine.phone_normalized is None
