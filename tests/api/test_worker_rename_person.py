"""Renaming a worker — or changing its phone — changes the Person behind it, within the company.

PUT /projects/<id>/workers/<wid> {name} updates the linked `persons` row when no
other company lists the person and no account is linked to them, so the new name
shows on every project of the company — worker lists, the day roster and the
company person search — not only on the worker row that was edited. A person
shared with another company, tied to an account, or listed by no company at all
is not rewritten, and every screen shows the Person's identity, so a different
name or phone for them is refused with 409 instead of being saved where nothing
shows it. Sending the current name or phone back unchanged still succeeds.
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


@pytest.fixture
def private_person(rename_app):
    """One person only company A lists, with two projects in that company."""
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
        email = f"rename-own-{uuid4().hex[:6]}@test.com"
        project_one = _company_with_admin_and_project(email, person.id)
        first = db.session.get(ProjectModel, project_one)
        second = ProjectModel(id=uuid4(), name="Second", owner_id=first.owner_id, company_id=first.company_id)
        db.session.add(second)
        db.session.commit()
        return {
            "person_id": str(person.id),
            "project_one": str(project_one),
            "project_two": str(second.id),
            "company_id": first.company_id,
            "admin": email,
        }


def _add_worker(client, token, project_id, person_id) -> dict:
    resp = client.post(f"/api/v1/projects/{project_id}/workers", json={"person_id": person_id}, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def test_renaming_a_worker_renames_a_person_only_its_company_lists(client, private_person):
    token = mint_access_token(client, private_person["admin"])
    worker_one = _add_worker(client, token, private_person["project_one"], private_person["person_id"])
    worker_two = _add_worker(client, token, private_person["project_two"], private_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{private_person['project_one']}/workers/{worker_one['id']}",
        json={"name": "Jean Dupont-Martin"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["name"] == "Jean Dupont-Martin"
    assert resp.get_json()["person_name"] == "Jean Dupont-Martin"

    # The company's other project shows the new name too.
    listed = client.get(f"/api/v1/projects/{private_person['project_two']}/workers", headers=_auth(token))
    assert listed.status_code == 200
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_two["id"])
    assert row["person_name"] == "Jean Dupont-Martin"
    assert row["name"] == "Jean Dupont-Martin"

    roster = client.get(
        f"/api/v1/projects/{private_person['project_two']}/labor/roster",
        query_string={"date": date.today().isoformat()},
        headers=_auth(token),
    )
    assert roster.status_code == 200, roster.get_data(as_text=True)
    assert [r["name"] for r in roster.get_json()["rows"] if r["worker_id"] == worker_two["id"]] == [
        "Jean Dupont-Martin"
    ]

    found = client.get("/api/v1/persons", query_string={"q": "dupont-martin"}, headers=_auth(token))
    assert found.status_code == 200
    assert private_person["person_id"] in [p["id"] for p in found.get_json()["persons"]]


def _assert_unchanged(client, token, project_id, worker_id, name="Jean Dupont", phone="+33611112222"):
    listed = client.get(f"/api/v1/projects/{project_id}/workers", headers=_auth(token))
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_id)
    assert (row["name"], row["person_name"]) == (name, name)
    assert (row["phone"], row["person_phone"]) == (phone, phone)


def test_renaming_a_worker_of_a_person_another_company_lists_is_refused(client, shared_person, rename_app):
    token_a = mint_access_token(client, shared_person["admin_a"])
    token_b = mint_access_token(client, shared_person["admin_b"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])
    worker_b = _add_worker(client, token_b, shared_person["project_b"], shared_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"name": "Jean Dupont-Martin"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "Conflict"

    # Nothing moved: neither company's list shows the attempted name.
    _assert_unchanged(client, token_a, shared_person["project_a"], worker_a["id"])
    _assert_unchanged(client, token_b, shared_person["project_b"], worker_b["id"])

    from app import db

    with rename_app.app_context():
        assert db.session.get(PersonModel, UUID(shared_person["person_id"])).name == "Jean Dupont"

    # The form sending the current name back is still a normal save.
    again = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"name": "Jean Dupont", "phone": "+33611112222"},
        headers=_auth(token_a),
    )
    assert again.status_code == 200, again.get_data(as_text=True)


def test_renaming_a_worker_never_renames_a_person_tied_to_an_account(client, private_person, rename_app):
    """The account holder owns their identity: a company cannot change it from a worker."""
    from app import db

    with rename_app.app_context():
        holder = UserModel(id=uuid4(), email=f"holder-{uuid4().hex[:6]}@test.com", is_active=True)
        db.session.add(holder)
        db.session.flush()
        db.session.get(PersonModel, UUID(private_person["person_id"])).user_id = holder.id
        db.session.commit()
    token = mint_access_token(client, private_person["admin"])
    worker = _add_worker(client, token, private_person["project_one"], private_person["person_id"])
    url = f"/api/v1/projects/{private_person['project_one']}/workers/{worker['id']}"

    for body in ({"name": "Renamed By Admin"}, {"phone": "+33699990000"}):
        resp = client.put(url, json=body, headers=_auth(token))
        assert resp.status_code == 409, resp.get_data(as_text=True)
    _assert_unchanged(client, token, private_person["project_one"], worker["id"])
    with rename_app.app_context():
        person = db.session.get(PersonModel, UUID(private_person["person_id"]))
        assert (person.name, person.phone) == ("Jean Dupont", "+33611112222")

    # The rest of the worker stays editable.
    ok = client.put(url, json={"name": "Jean Dupont", "phone": "+33611112222"}, headers=_auth(token))
    assert ok.status_code == 200, ok.get_data(as_text=True)


def test_renaming_a_worker_whose_person_no_company_lists_is_refused(client, private_person, rename_app):
    """No directory row proves the person is private to this company, so the edit is refused."""
    from app import db

    token = mint_access_token(client, private_person["admin"])
    worker = _add_worker(client, token, private_person["project_one"], private_person["person_id"])
    with rename_app.app_context():
        db.session.query(CompanyPersonModel).filter_by(person_id=UUID(private_person["person_id"])).delete()
        db.session.commit()

    resp = client.put(
        f"/api/v1/projects/{private_person['project_one']}/workers/{worker['id']}",
        json={"name": "Jean Dupont-Martin"},
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    _assert_unchanged(client, token, private_person["project_one"], worker["id"])


def test_changing_a_workers_phone_changes_a_person_only_its_company_lists(client, private_person, rename_app):
    token = mint_access_token(client, private_person["admin"])
    worker_one = _add_worker(client, token, private_person["project_one"], private_person["person_id"])
    worker_two = _add_worker(client, token, private_person["project_two"], private_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{private_person['project_one']}/workers/{worker_one['id']}",
        json={"phone": "06 12 34 56 78"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["phone"] == "06 12 34 56 78"
    assert resp.get_json()["person_phone"] == "06 12 34 56 78"

    # The company's other project shows the new phone too.
    listed = client.get(f"/api/v1/projects/{private_person['project_two']}/workers", headers=_auth(token))
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_two["id"])
    assert row["person_phone"] == "06 12 34 56 78" and row["phone"] == "06 12 34 56 78"

    from app import db

    with rename_app.app_context():
        person = db.session.get(PersonModel, UUID(private_person["person_id"]))
        assert person.phone == "06 12 34 56 78" and person.phone_normalized == "+33612345678"
        hints = {
            cp.phone_normalized for cp in db.session.query(CompanyPersonModel).filter_by(person_id=person.id).all()
        }
        assert hints == {"+33612345678"}

    # Cleared the same way.
    cleared = client.put(
        f"/api/v1/projects/{private_person['project_one']}/workers/{worker_one['id']}",
        json={"phone": ""},
        headers=_auth(token),
    )
    assert cleared.status_code == 200, cleared.get_data(as_text=True)
    with rename_app.app_context():
        db.session.expire_all()
        assert db.session.get(PersonModel, UUID(private_person["person_id"])).phone is None


def test_changing_a_workers_phone_for_a_person_another_company_lists_is_refused(client, shared_person, rename_app):
    token_a = mint_access_token(client, shared_person["admin_a"])
    token_b = mint_access_token(client, shared_person["admin_b"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])
    worker_b = _add_worker(client, token_b, shared_person["project_b"], shared_person["person_id"])

    resp = client.put(
        f"/api/v1/projects/{shared_person['project_a']}/workers/{worker_a['id']}",
        json={"phone": "06 12 34 56 78"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    _assert_unchanged(client, token_a, shared_person["project_a"], worker_a["id"])
    _assert_unchanged(client, token_b, shared_person["project_b"], worker_b["id"])

    from app import db

    with rename_app.app_context():
        person = db.session.get(PersonModel, UUID(shared_person["person_id"]))
        assert person.phone == "+33611112222"
        hints = {
            cp.phone_normalized for cp in db.session.query(CompanyPersonModel).filter_by(person_id=person.id).all()
        }
        assert "+33612345678" not in hints


def test_a_company_cannot_link_a_worker_to_a_person_only_another_company_lists(client, shared_person, rename_app):
    """The id of a person in a rival's directory opens neither their data nor their rows."""
    from app import db

    with rename_app.app_context():
        profile = (
            db.session.query(CompanyPersonModel)
            .filter_by(person_id=UUID(shared_person["person_id"]))
            .order_by(CompanyPersonModel.created_at)
            .all()[-1]
        )
        db.session.delete(profile)  # leave the person in company A's directory alone
        db.session.commit()
    token_a = mint_access_token(client, shared_person["admin_a"])
    token_b = mint_access_token(client, shared_person["admin_b"])
    worker_a = _add_worker(client, token_a, shared_person["project_a"], shared_person["person_id"])

    attack = client.post(
        f"/api/v1/projects/{shared_person['project_b']}/workers",
        json={"person_id": shared_person["person_id"]},
        headers=_auth(token_b),
    )
    assert attack.status_code == 404, attack.get_data(as_text=True)
    assert "Jean" not in attack.get_data(as_text=True) and "33611112222" not in attack.get_data(as_text=True)
    missing = client.post(
        f"/api/v1/projects/{shared_person['project_b']}/workers",
        json={"person_id": str(uuid4())},
        headers=_auth(token_b),
    )
    assert (missing.status_code, missing.get_json()) == (attack.status_code, attack.get_json())
    listed = client.get(f"/api/v1/projects/{shared_person['project_a']}/workers", headers=_auth(token_a))
    row = next(w for w in listed.get_json()["workers"] if w["id"] == worker_a["id"])
    assert row["name"] == "Jean Dupont"


def test_a_number_another_profile_holds_in_a_company_is_not_duplicated_there(client, private_person, rename_app):
    """The directory allows one active profile per number and company; the person keeps the phone."""
    from app import db

    token_a = mint_access_token(client, private_person["admin"])
    worker_a = _add_worker(client, token_a, private_person["project_one"], private_person["person_id"])
    with rename_app.app_context():
        profile = db.session.query(CompanyPersonModel).filter_by(person_id=UUID(private_person["person_id"])).first()
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
        f"/api/v1/projects/{private_person['project_one']}/workers/{worker_a['id']}",
        json={"phone": "+33687654321"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    with rename_app.app_context():
        db.session.expire_all()
        assert db.session.get(PersonModel, UUID(private_person["person_id"])).phone == "+33687654321"
        mine = (
            db.session.query(CompanyPersonModel)
            .filter_by(person_id=UUID(private_person["person_id"]), company_id=company_id)
            .one()
        )
        assert mine.phone_normalized is None


def _store_raw_phone(rename_app, person_id: str, raw: str = "06 12 34 56 78") -> None:
    """A phone saved as typed, the way the web form and the picker store it."""
    from app import db

    with rename_app.app_context():
        db.session.get(PersonModel, UUID(person_id)).phone = raw
        db.session.commit()


def _assert_person_unchanged(rename_app, person_id: str, phone: str = "06 12 34 56 78") -> None:
    from app import db

    with rename_app.app_context():
        db.session.expire_all()
        person = db.session.get(PersonModel, UUID(person_id))
        assert (person.name, person.phone) == ("Jean Dupont", phone)


def test_a_form_resending_the_normalised_phone_still_saves_a_shared_person(client, shared_person, rename_app):
    """The mobile form sends the E.164 form of the stored '06 12 34 56 78' together with the name."""
    _store_raw_phone(rename_app, shared_person["person_id"])
    token = mint_access_token(client, shared_person["admin_a"])
    worker = _add_worker(client, token, shared_person["project_a"], shared_person["person_id"])
    url = f"/api/v1/projects/{shared_person['project_a']}/workers/{worker['id']}"

    resp = client.put(url, json={"name": "Jean Dupont", "phone": "+33612345678"}, headers=_auth(token))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["person_phone"] == "06 12 34 56 78"
    _assert_person_unchanged(rename_app, shared_person["person_id"])

    # The same number spelled another way, or the name re-cased and re-spaced, is no change either.
    for body in (
        {"name": "Jean Dupont", "phone": "0612345678"},
        {"name": "  jean   DUPONT ", "phone": "+33612345678"},
    ):
        again = client.put(url, json=body, headers=_auth(token))
        assert again.status_code == 200, again.get_data(as_text=True)
    _assert_person_unchanged(rename_app, shared_person["person_id"])

    # A genuinely different number or name is still refused.
    for body in ({"phone": "+33699999999"}, {"name": "Jean Durand"}, {"name": "Jean Dupont", "phone": "+33699999999"}):
        refused = client.put(url, json=body, headers=_auth(token))
        assert refused.status_code == 409, refused.get_data(as_text=True)
    _assert_person_unchanged(rename_app, shared_person["person_id"])


def test_a_form_resending_the_normalised_phone_still_saves_an_account_linked_person(client, private_person, rename_app):
    from app import db

    _store_raw_phone(rename_app, private_person["person_id"])
    with rename_app.app_context():
        holder = UserModel(id=uuid4(), email=f"holder-{uuid4().hex[:6]}@test.com", is_active=True)
        db.session.add(holder)
        db.session.flush()
        db.session.get(PersonModel, UUID(private_person["person_id"])).user_id = holder.id
        db.session.commit()
    token = mint_access_token(client, private_person["admin"])
    worker = _add_worker(client, token, private_person["project_one"], private_person["person_id"])
    url = f"/api/v1/projects/{private_person['project_one']}/workers/{worker['id']}"

    resp = client.put(url, json={"name": "Jean Dupont", "phone": "+33612345678"}, headers=_auth(token))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _assert_person_unchanged(rename_app, private_person["person_id"])

    refused = client.put(url, json={"name": "Jean Dupont", "phone": "+33699999999"}, headers=_auth(token))
    assert refused.status_code == 409, refused.get_data(as_text=True)
    _assert_person_unchanged(rename_app, private_person["person_id"])
