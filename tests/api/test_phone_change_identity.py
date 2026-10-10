"""A verified phone change moves the number everywhere it names the account.

Before: only ``users.phone`` changed. A phone sign-up kept its ``phone-<old number>@`` address, so the
old number's next sign-up hit the unique e-mail (500), and the account's person in company directories
kept the old number, which still counted as taken there (409 naming internal ids).

Built on a fresh ``create_app(TestingConfig)`` app so the real DI wiring (person repository injected
into the phone change) is what runs.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from tests.api.test_company_members_endpoints import (
    _auth,
    _link_person_to_company,
    _login,
    _make_company,
    _make_person,
    _make_user,
)

CODE = "424242"
DOMAIN = "no-email.folio.flowitup.com"


@pytest.fixture(scope="module")
def identity_app():
    from app import create_app, db
    from config import TestingConfig

    class IdentityTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(IdentityTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(identity_app):
    return identity_app.test_client()


@pytest.fixture(autouse=True)
def _test_code(monkeypatch):
    """Codes are checked against the non-production test code instead of reading SMS."""
    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("OTP_TEST_CODE", CODE)


def _sign_up(client, phone: str, name: str) -> dict:
    assert client.post("/api/v1/auth/signup/request", json={"phone": phone}).status_code == 202
    resp = client.post("/api/v1/auth/signup/verify", json={"phone": phone, "code": CODE, "display_name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _change_phone(client, token: str, phone: str) -> dict:
    resp = client.post("/api/v1/auth/me/phone/request-code", json={"phone": phone}, headers=_auth(token))
    assert resp.status_code == 202, resp.get_json()
    resp = client.post("/api/v1/auth/me/phone/confirm", json={"phone": phone, "code": CODE}, headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_the_old_number_can_sign_up_again(client, identity_app):
    from app import db
    from app.infrastructure.database.models import LoginOtpOrm

    old, new = "+33900000711", "+33900000712"
    account = _sign_up(client, old, "Lan")
    assert account["user"]["email"] == f"phone-33900000711@{DOMAIN}"

    changed = _change_phone(client, account["access_token"], new)
    assert changed["phone"] == new
    assert changed["email"] == f"phone-33900000712@{DOMAIN}"

    with identity_app.app_context():
        db.session.query(LoginOtpOrm).delete()  # skip the one-minute resend gap on the old number
        db.session.commit()
    newcomer = _sign_up(client, old, "Minh")
    assert newcomer["user"]["phone"] == old and newcomer["user"]["id"] != account["user"]["id"]
    assert newcomer["user"]["email"] == f"phone-33900000711@{DOMAIN}"


def test_sign_up_reclaims_the_address_an_older_phone_change_left_behind(client, identity_app):
    """Accounts that changed number before the address followed it still hold the old one."""
    from app import db

    stale_id = uuid4()
    with identity_app.app_context():
        db.session.add(
            UserModel(id=stale_id, email=f"phone-33900000721@{DOMAIN}", is_active=True, phone="+33900000722")
        )
        db.session.commit()

    newcomer = _sign_up(client, "+33900000721", "Hoa")
    assert newcomer["user"]["email"] == f"phone-33900000721@{DOMAIN}"
    with identity_app.app_context():
        assert db.session.get(UserModel, stale_id).email == f"phone-33900000722@{DOMAIN}"


def test_the_directory_person_moves_to_the_new_number(client, identity_app):
    from app import db

    old, new = "+33900000731", "+33900000732"
    admin_id = _make_user(identity_app, "identity_admin@test.com")
    company_id = _make_company(identity_app, admin_id, name="Identity Co")
    admin_token = _login(client, "identity_admin@test.com")
    account = _sign_up(client, old, "Tuan")
    added = client.post(
        f"/api/v1/companies/{company_id}/members", json={"phone": old, "role": "member"}, headers=_auth(admin_token)
    )
    assert added.status_code == 201, added.get_json()
    person_id = UUID(added.get_json()["person_id"])

    _change_phone(client, account["access_token"], new)

    with identity_app.app_context():
        person = db.session.get(PersonModel, person_id)
        assert (person.phone, person.phone_normalized) == (new, new)
        profile = db.session.query(CompanyPersonModel).filter_by(person_id=person_id).one()
        assert profile.phone_normalized == new
    directory = client.get(f"/api/v1/companies/{company_id}/persons", headers=_auth(admin_token)).get_json()
    assert {item["phone"] for item in directory["items"] if item["person_id"] == str(person_id)} == {new}

    # The old number is free in the company: adding it creates someone new rather than a 409.
    again = client.post(
        f"/api/v1/companies/{company_id}/members", json={"phone": old, "role": "member"}, headers=_auth(admin_token)
    )
    assert again.status_code == 201, again.get_json()
    assert again.get_json()["person_id"] != str(person_id)


def test_the_new_number_already_on_someone_elses_profile_is_not_copied(client, identity_app):
    """One profile per number and company (removed ones included): the change goes through all the same."""
    from app import db

    old, new = "+33900000741", "+33900000742"
    admin_id = _make_user(identity_app, "identity_admin2@test.com")
    company_id = _make_company(identity_app, admin_id, name="Identity Co 2")
    admin_token = _login(client, "identity_admin2@test.com")
    account = _sign_up(client, old, "Binh")
    added = client.post(
        f"/api/v1/companies/{company_id}/members", json={"phone": old, "role": "member"}, headers=_auth(admin_token)
    )
    person_id = UUID(added.get_json()["person_id"])
    # Someone removed from the company long ago still carries the number on their profile.
    former = _make_person(identity_app, name="Former", phone_normalized=new)
    _link_person_to_company(identity_app, company_id, former, pending=False, phone_normalized=new)
    with identity_app.app_context():
        db.session.query(CompanyPersonModel).filter_by(person_id=former).update({"is_active": False})
        db.session.commit()

    _change_phone(client, account["access_token"], new)

    with identity_app.app_context():
        assert db.session.get(PersonModel, person_id).phone == new
        assert db.session.query(CompanyPersonModel).filter_by(person_id=person_id).one().phone_normalized is None


def test_a_number_taken_in_the_company_is_refused_without_internal_ids(client, identity_app):
    owner_id = _make_user(identity_app, "identity_owner@test.com")
    admin_id = _make_user(identity_app, "identity_admin3@test.com")
    company_id = _make_company(identity_app, admin_id, name="Identity Co 3")
    taken = "+33900000751"
    person_id = _make_person(identity_app, name="Owner", phone_normalized=taken, user_id=owner_id)
    _link_person_to_company(identity_app, company_id, person_id, pending=False, phone_normalized=taken)

    resp = client.post(
        f"/api/v1/companies/{company_id}/members",
        json={"phone": taken, "role": "member"},
        headers=_auth(_login(client, "identity_admin3@test.com")),
    )
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["message"] == "This phone number already belongs to someone in this company."
    assert body["person_id"] == str(person_id)
