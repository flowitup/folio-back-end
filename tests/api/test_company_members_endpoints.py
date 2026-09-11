"""API tests for the member onboarding endpoints (Phase 2 onboarding slice B):

  POST /companies/<id>/members         — add by phone (match order a/b/c/d)
  POST /companies/<id>/members/import  — import from another company
  GET  /companies/<id>/persons         — directory (admin or manager)

Built on a fresh `create_app(TestingConfig)` app (NOT the shared
`invitation_app` conftest fixture, which manually re-wires the companies DI
block and would need to duplicate every new use case here too).
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
def members_app():
    from app import create_app, db
    from config import TestingConfig

    class MembersTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(MembersTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def members_client(members_app):
    return members_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


def _make_user(app, email: str, *, phone: str | None = None, display_name: str | None = None) -> str:
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True, phone=phone, display_name=display_name)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_company(app, admin_user_id: str, *, name: str) -> str:
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(),
            legal_name=name,
            address="1 rue de Test",
            created_by=admin_user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_user_id,
                company_id=company.id,
                role="admin",
                is_primary=True,
                attached_at=now,
            )
        )
        db.session.commit()
        return company.id


def _make_person(app, *, name: str, phone_normalized: str | None, user_id: str | None = None) -> str:
    from app import db

    with app.app_context():
        person = PersonModel(
            id=uuid4(),
            name=name,
            normalized_name=name.lower(),
            created_by_user_id=user_id or uuid4(),
            created_at=datetime.now(timezone.utc),
            phone=phone_normalized,
            phone_normalized=phone_normalized,
            user_id=user_id,
        )
        db.session.add(person)
        db.session.commit()
        return person.id


def _link_person_to_company(
    app, company_id: str, person_id: str, *, pending: bool = True, phone_normalized: str | None = None
) -> None:
    """Create a company_persons row. `phone_normalized` mirrors the value the
    real add-by-phone use case always stores on this table (`find_by_phone`
    queries `company_persons.phone_normalized`, not `persons.phone_normalized`)
    — callers testing match order (b)/(c) must pass the same phone here."""
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(
            CompanyPersonModel(
                id=uuid4(),
                company_id=company_id,
                person_id=person_id,
                is_active=True,
                created_at=now,
                phone_normalized=phone_normalized,
                pending_expires_at=(now.replace(year=now.year + 1)) if pending else None,
            )
        )
        db.session.commit()


class TestAddMemberByPhoneExistingAccount:
    """Match order (a): an existing user account with this phone."""

    def test_attaches_existing_account_immediately(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin1@test.com")
        target_id = _make_user(members_app, "mab_target1@test.com", phone="+33611110001", display_name="Target One")
        company_id = _make_company(members_app, admin_id, name="MAB Co 1")
        token = _login(members_client, "mab_admin1@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_id}/members",
            json={"phone": "0611110001", "role": "member"},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        # M6: response shape never exposes `pending` — identical whether the
        # phone matched an existing account or a brand new profile.
        assert set(body.keys()) == {"person_id", "name", "phone"}
        assert body["name"] == "Target One"

        from app import db

        with members_app.app_context():
            access = db.session.get(UserCompanyAccessModel, (target_id, company_id))
            assert access is not None and access.role == "member"

    def test_admin_role_rejected(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin2@test.com")
        _make_user(members_app, "mab_target2@test.com", phone="+33611110002")
        company_id = _make_company(members_app, admin_id, name="MAB Co 2")
        token = _login(members_client, "mab_admin2@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_id}/members",
            json={"phone": "0611110002", "role": "admin"},
            headers=_auth(token),
        )
        assert resp.status_code == 400

    def test_non_admin_caller_forbidden(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin3@test.com")
        member_id = _make_user(members_app, "mab_member3@test.com")
        company_id = _make_company(members_app, admin_id, name="MAB Co 3")
        from app import db

        with members_app.app_context():
            db.session.add(
                UserCompanyAccessModel(
                    user_id=member_id,
                    company_id=company_id,
                    role="member",
                    is_primary=True,
                    attached_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()
        token = _login(members_client, "mab_member3@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_id}/members",
            json={"phone": "0611119999", "role": "member"},
            headers=_auth(token),
        )
        assert resp.status_code == 403


class TestAddMemberByPhoneMatchOrder:
    """Match order (b)/(c)/(d): un-linked profile, multiple candidates, brand new."""

    def test_links_unlinked_profile_from_another_admin_company(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin4@test.com")
        company_a = _make_company(members_app, admin_id, name="MAB Co 4a")
        company_b = _make_company(members_app, admin_id, name="MAB Co 4b")
        person_id = _make_person(members_app, name="Un Linked", phone_normalized="+33611110010")
        _link_person_to_company(members_app, company_b, person_id, pending=False, phone_normalized="+33611110010")
        token = _login(members_client, "mab_admin4@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_a}/members",
            json={"phone": "0611110010"},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["person_id"] == str(person_id)
        assert set(body.keys()) == {"person_id", "name", "phone"}

    def test_several_candidates_return_409_then_resend_with_person_id(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin5@test.com")
        company_a = _make_company(members_app, admin_id, name="MAB Co 5a")
        company_b = _make_company(members_app, admin_id, name="MAB Co 5b")
        company_c = _make_company(members_app, admin_id, name="MAB Co 5c")
        person_b = _make_person(members_app, name="Candidate B", phone_normalized="+33611110020")
        person_c = _make_person(members_app, name="Candidate C", phone_normalized="+33611110020")
        _link_person_to_company(members_app, company_b, person_b, phone_normalized="+33611110020")
        _link_person_to_company(members_app, company_c, person_c, phone_normalized="+33611110020")
        token = _login(members_client, "mab_admin5@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_a}/members",
            json={"phone": "0611110020"},
            headers=_auth(token),
        )
        assert resp.status_code == 409
        candidates = resp.get_json()["candidates"]
        assert {c["person_id"] for c in candidates} == {str(person_b), str(person_c)}

        resp2 = members_client.post(
            f"/api/v1/companies/{company_a}/members",
            json={"phone": "0611110020", "person_id": str(person_b)},
            headers=_auth(token),
        )
        assert resp2.status_code == 201
        assert resp2.get_json()["person_id"] == str(person_b)

    def test_no_match_creates_brand_new_pending_profile(self, members_client, members_app):
        admin_id = _make_user(members_app, "mab_admin6@test.com")
        company_id = _make_company(members_app, admin_id, name="MAB Co 6")
        token = _login(members_client, "mab_admin6@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{company_id}/members",
            json={"phone": "0611110030", "name": "Brand New"},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["name"] == "Brand New"
        # (a) and (d) share the exact same response shape — no enumeration,
        # and `pending` is never exposed here (M6; the directory keeps it).
        assert set(body.keys()) == {"person_id", "name", "phone"}


class TestImportMembers:
    def test_import_copies_profile_and_attaches_linked_user(self, members_client, members_app):
        admin_id = _make_user(members_app, "imp_admin1@test.com")
        linked_user_id = _make_user(members_app, "imp_linked1@test.com", phone="+33611110040")
        source_company = _make_company(members_app, admin_id, name="Import Source Co")
        target_company = _make_company(members_app, admin_id, name="Import Target Co")
        person_id = _make_person(
            members_app, name="Imported Person", phone_normalized="+33611110040", user_id=linked_user_id
        )
        _link_person_to_company(members_app, source_company, person_id, pending=False)
        token = _login(members_client, "imp_admin1@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{target_company}/members/import",
            json={"from_company_id": source_company, "person_ids": [person_id]},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        items = resp.get_json()["items"]
        assert len(items) == 1
        assert items[0]["linked_user_id"] == str(linked_user_id)

        from app import db

        with members_app.app_context():
            access = db.session.get(UserCompanyAccessModel, (linked_user_id, target_company))
            assert access is not None and access.role == "member"

    def test_import_requires_admin_of_source_company(self, members_client, members_app):
        admin_id = _make_user(members_app, "imp_admin2@test.com")
        other_admin_id = _make_user(members_app, "imp_other_admin2@test.com")
        target_company = _make_company(members_app, admin_id, name="Import Target Co 2")
        source_company = _make_company(members_app, other_admin_id, name="Import Source Co 2")
        person_id = _make_person(members_app, name="Not Yours", phone_normalized="+33611110050")
        _link_person_to_company(members_app, source_company, person_id, pending=False)
        token = _login(members_client, "imp_admin2@test.com")

        resp = members_client.post(
            f"/api/v1/companies/{target_company}/members/import",
            json={"from_company_id": source_company, "person_ids": [person_id]},
            headers=_auth(token),
        )
        assert resp.status_code == 403


class TestDirectoryScoping:
    def test_directory_scoped_to_company_admin_or_manager(self, members_client, members_app):
        admin_id = _make_user(members_app, "dir_admin1@test.com")
        manager_id = _make_user(members_app, "dir_manager1@test.com")
        member_id = _make_user(members_app, "dir_member1@test.com")
        company_a = _make_company(members_app, admin_id, name="Directory Co A")
        company_b = _make_company(members_app, admin_id, name="Directory Co B")
        person_a = _make_person(members_app, name="Only In A", phone_normalized="+33611110060")
        person_b = _make_person(members_app, name="Only In B", phone_normalized="+33611110070")
        _link_person_to_company(members_app, company_a, person_a, pending=False)
        _link_person_to_company(members_app, company_b, person_b, pending=False)

        from app import db

        now = datetime.now(timezone.utc)
        with members_app.app_context():
            db.session.add_all(
                [
                    UserCompanyAccessModel(
                        user_id=manager_id, company_id=company_a, role="manager", is_primary=True, attached_at=now
                    ),
                    UserCompanyAccessModel(
                        user_id=member_id, company_id=company_a, role="member", is_primary=True, attached_at=now
                    ),
                ]
            )
            db.session.commit()

        admin_token = _login(members_client, "dir_admin1@test.com")
        manager_token = _login(members_client, "dir_manager1@test.com")
        member_token = _login(members_client, "dir_member1@test.com")

        resp = members_client.get(f"/api/v1/companies/{company_a}/persons", headers=_auth(admin_token))
        assert resp.status_code == 200
        names = {item["name"] for item in resp.get_json()["items"]}
        assert names == {"Only In A"}

        resp_mgr = members_client.get(f"/api/v1/companies/{company_a}/persons", headers=_auth(manager_token))
        assert resp_mgr.status_code == 200

        resp_member = members_client.get(f"/api/v1/companies/{company_a}/persons", headers=_auth(member_token))
        assert resp_member.status_code == 403


def _make_labor_role(app, *, company_id: str | None, name: str) -> str:
    """A labor role owned by `company_id` (None = the legacy unscoped kind)."""
    from app import db
    from app.infrastructure.database.models.labor_role import LaborRoleModel

    with app.app_context():
        role = LaborRoleModel(
            id=uuid4(),
            company_id=company_id,
            name=name,
            color="#123456",
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(role)
        db.session.commit()
        return role.id


def _read_profile(app, company_id: str, person_id: str):
    from app import db

    with app.app_context():
        return db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=person_id).one_or_none()


class TestUpdateMemberPayDefaults:
    """PATCH /companies/<id>/members/<person_id> — the only writer of the pay
    defaults a new project Worker inherits (`default_daily_rate`,
    `labor_role_id`). Before it, only the one-off backfill ever set them."""

    def test_admin_sets_rate_and_role_and_the_directory_shows_them(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin1@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 1")
        person_id = _make_person(members_app, name="Pay One", phone_normalized="+33622220001")
        _link_person_to_company(members_app, company_id, person_id, pending=False)
        role_id = _make_labor_role(members_app, company_id=company_id, name="Thợ chính")
        token = _login(members_client, "pay_admin1@test.com")

        resp = members_client.patch(
            f"/api/v1/companies/{company_id}/members/{person_id}",
            json={"default_daily_rate": 145.5, "labor_role_id": str(role_id)},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["default_daily_rate"] == 145.5
        assert body["labor_role_id"] == str(role_id)

        profile = _read_profile(members_app, company_id, person_id)
        assert float(profile.default_daily_rate) == 145.5
        assert str(profile.labor_role_id) == str(role_id)

        # The directory is where every client reads these back.
        listed = members_client.get(f"/api/v1/companies/{company_id}/persons", headers=_auth(token))
        entry = next(i for i in listed.get_json()["items"] if i["person_id"] == str(person_id))
        assert entry["default_daily_rate"] == 145.5
        assert entry["labor_role_id"] == str(role_id)

    def test_omitted_field_is_kept_while_explicit_null_clears_it(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin2@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 2")
        person_id = _make_person(members_app, name="Pay Two", phone_normalized="+33622220002")
        _link_person_to_company(members_app, company_id, person_id, pending=False)
        role_id = _make_labor_role(members_app, company_id=company_id, name="Thợ phụ")
        token = _login(members_client, "pay_admin2@test.com")
        url = f"/api/v1/companies/{company_id}/members/{person_id}"

        members_client.patch(url, json={"default_daily_rate": 120, "labor_role_id": str(role_id)}, headers=_auth(token))

        # Only the rate is in the body: the role must survive untouched.
        resp = members_client.patch(url, json={"default_daily_rate": 130}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["labor_role_id"] == str(role_id)

        # An explicit null is a different request: it clears.
        resp = members_client.patch(url, json={"labor_role_id": None}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["labor_role_id"] is None
        assert resp.get_json()["default_daily_rate"] == 130.0

        resp = members_client.patch(url, json={"default_daily_rate": None}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["default_daily_rate"] is None

        # An empty body touches nothing rather than wiping the row.
        profile_before = _read_profile(members_app, company_id, person_id)
        assert members_client.patch(url, json={}, headers=_auth(token)).status_code == 200
        profile_after = _read_profile(members_app, company_id, person_id)
        assert profile_after.default_daily_rate == profile_before.default_daily_rate
        assert profile_after.labor_role_id == profile_before.labor_role_id

    def test_manager_may_write_member_may_not(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin3@test.com")
        manager_id = _make_user(members_app, "pay_manager3@test.com")
        plain_id = _make_user(members_app, "pay_member3@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 3")
        person_id = _make_person(members_app, name="Pay Three", phone_normalized="+33622220003")
        _link_person_to_company(members_app, company_id, person_id, pending=False)

        from app import db

        now = datetime.now(timezone.utc)
        with members_app.app_context():
            db.session.add_all(
                [
                    UserCompanyAccessModel(
                        user_id=manager_id, company_id=company_id, role="manager", is_primary=True, attached_at=now
                    ),
                    UserCompanyAccessModel(
                        user_id=plain_id, company_id=company_id, role="member", is_primary=True, attached_at=now
                    ),
                ]
            )
            db.session.commit()

        url = f"/api/v1/companies/{company_id}/members/{person_id}"

        manager = members_client.patch(
            url, json={"default_daily_rate": 99}, headers=_auth(_login(members_client, "pay_manager3@test.com"))
        )
        assert manager.status_code == 200, manager.get_data(as_text=True)

        member = members_client.patch(
            url, json={"default_daily_rate": 1}, headers=_auth(_login(members_client, "pay_member3@test.com"))
        )
        assert member.status_code == 403
        # The refused write left the manager's value alone.
        assert float(_read_profile(members_app, company_id, person_id).default_daily_rate) == 99.0

    def test_rejects_a_labor_role_owned_by_another_company(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin4@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 4")
        other_company_id = _make_company(members_app, admin_id, name="Pay Co 4 Other")
        person_id = _make_person(members_app, name="Pay Four", phone_normalized="+33622220004")
        _link_person_to_company(members_app, company_id, person_id, pending=False)
        foreign_role = _make_labor_role(members_app, company_id=other_company_id, name="Foreign Role")
        legacy_role = _make_labor_role(members_app, company_id=None, name="Legacy Role")
        token = _login(members_client, "pay_admin4@test.com")
        url = f"/api/v1/companies/{company_id}/members/{person_id}"

        foreign = members_client.patch(url, json={"labor_role_id": str(foreign_role)}, headers=_auth(token))
        assert foreign.status_code == 400
        assert foreign.get_json()["error"] == "InvalidInput"

        # A legacy unscoped role belongs to no company's list either.
        legacy = members_client.patch(url, json={"labor_role_id": str(legacy_role)}, headers=_auth(token))
        assert legacy.status_code == 400

        unknown = members_client.patch(url, json={"labor_role_id": str(uuid4())}, headers=_auth(token))
        assert unknown.status_code == 400

        assert _read_profile(members_app, company_id, person_id).labor_role_id is None

    def test_404_for_a_person_who_is_not_a_member_or_was_booted(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin5@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 5")
        stranger_id = _make_person(members_app, name="Pay Stranger", phone_normalized="+33622220005")
        booted_id = _make_person(members_app, name="Pay Booted", phone_normalized="+33622220006")
        _link_person_to_company(members_app, company_id, booted_id, pending=False)
        token = _login(members_client, "pay_admin5@test.com")

        from app import db

        with members_app.app_context():
            row = db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=booted_id).one()
            row.is_active = False
            db.session.commit()

        stranger = members_client.patch(
            f"/api/v1/companies/{company_id}/members/{stranger_id}",
            json={"default_daily_rate": 100},
            headers=_auth(token),
        )
        assert stranger.status_code == 404

        booted = members_client.patch(
            f"/api/v1/companies/{company_id}/members/{booted_id}",
            json={"default_daily_rate": 100},
            headers=_auth(token),
        )
        assert booted.status_code == 404
        assert _read_profile(members_app, company_id, booted_id).default_daily_rate is None

    def test_rejects_a_non_positive_or_oversized_rate(self, members_client, members_app):
        admin_id = _make_user(members_app, "pay_admin6@test.com")
        company_id = _make_company(members_app, admin_id, name="Pay Co 6")
        person_id = _make_person(members_app, name="Pay Six", phone_normalized="+33622220007")
        _link_person_to_company(members_app, company_id, person_id, pending=False)
        token = _login(members_client, "pay_admin6@test.com")
        url = f"/api/v1/companies/{company_id}/members/{person_id}"

        for bad in (0, -5, 100000000):
            resp = members_client.patch(url, json={"default_daily_rate": bad}, headers=_auth(token))
            assert resp.status_code == 422, f"rate {bad} should be refused: {resp.get_data(as_text=True)}"

        # Unknown keys are refused too (strict schema), so a typo cannot silently no-op.
        typo = members_client.patch(url, json={"daily_rate": 120}, headers=_auth(token))
        assert typo.status_code == 422

        assert _read_profile(members_app, company_id, person_id).default_daily_rate is None
