"""Regression tests for the member life cycle around sign-up linking and primary companies:

  - an admin cancels a pending member added by phone (DELETE /companies/<id>/members/<person_id>),
    so a later sign-up with that number does not join the company;
  - importing a pending person (no account yet) from another company keeps the copy pending,
    so their sign-up joins both companies;
  - deleting a company re-promotes a primary company for the members whose primary it was.

Fresh `create_app()` app, so the full DI wiring in `app/__init__.py` applies. One session spans
the requests of a test: write paths are checked after a `db.session.rollback()` to prove they
committed (same caveat as `test_company_persons_review_fixes.py`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def lc_app():
    from app import create_app, db
    from config import TestingConfig

    class LcTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(LcTestConfig)
    with test_app.app_context():
        db.create_all()
        from wiring import get_container

        sent: list[tuple[str, str]] = []
        get_container().sms_sender.send = lambda to, text: sent.append((to, text))
        test_app._sms_sent = sent
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def lc_client(lc_app):
    return lc_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user(app, email: str, *, platform_ops: bool = False) -> UUID:
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True, is_platform_ops=platform_ops)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_company(app, name: str) -> UUID:
    from app import db

    now = datetime.now(timezone.utc)
    creator = _make_user(app, f"creator-{uuid4().hex[:8]}@test.com")
    with app.app_context():
        company = CompanyModel(
            id=uuid4(), legal_name=name, address="1 rue", created_by=creator, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.commit()
        return company.id


def _attach(app, user_id, company_id, *, role: str, is_primary: bool, attached_at: datetime | None = None) -> None:
    from app import db

    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id,
                company_id=company_id,
                role=role,
                is_primary=is_primary,
                attached_at=attached_at or datetime.now(timezone.utc),
            )
        )
        db.session.commit()


def _signup(client, app, phone: str, name: str) -> dict:
    assert client.post("/api/v1/auth/signup/request", json={"phone": phone}).status_code == 202
    for to, text in reversed(app._sms_sent):
        if to.endswith(phone[-9:]):
            code = re.search(r"\b(\d{6})\b", text).group(1)
            break
    else:  # pragma: no cover - the request above always sends one
        raise AssertionError(f"no SMS to {phone}")
    resp = client.post("/api/v1/auth/signup/verify", json={"phone": phone, "code": code, "display_name": name})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _companies_of(client, token: str) -> dict:
    items = client.get("/api/v1/companies", headers=_auth(token)).get_json()["items"]
    return {m["company"]["id"]: m["access"] for m in items}


@pytest.fixture
def admin(lc_app, lc_client):
    """A fresh admin of two fresh companies (A and C)."""
    admin_id = _make_user(lc_app, f"admin-{uuid4().hex[:8]}@test.com")
    company_a = _make_company(lc_app, "LC Co A")
    company_c = _make_company(lc_app, "LC Co C")
    _attach(lc_app, admin_id, company_a, role="admin", is_primary=True)
    _attach(lc_app, admin_id, company_c, role="admin", is_primary=False)
    from app import db

    with lc_app.app_context():
        email = db.session.get(UserModel, admin_id).email
    token = mint_access_token(lc_client, email)
    return {"id": admin_id, "token": token, "a": company_a, "c": company_c}


class TestCancelPendingMember:
    def test_cancelled_invitation_no_longer_joins_on_signup(self, lc_client, lc_app, admin):
        added = lc_client.post(
            f"/api/v1/companies/{admin['a']}/members",
            json={"phone": "0620350088", "name": "Typo Quebec", "role": "manager"},
            headers=_auth(admin["token"]),
        )
        assert added.status_code == 201, added.get_data(as_text=True)
        person_id = added.get_json()["person_id"]

        resp = lc_client.delete(f"/api/v1/companies/{admin['a']}/members/{person_id}", headers=_auth(admin["token"]))
        assert resp.status_code == 204, resp.get_data(as_text=True)

        from app import db

        db.session.rollback()
        directory = lc_client.get(f"/api/v1/companies/{admin['a']}/persons", headers=_auth(admin["token"])).get_json()
        entry = next(e for e in directory["items"] if e["person_id"] == person_id)
        assert entry["is_active"] is False and entry["pending"] is False

        # Cancelling again: no longer a member.
        again = lc_client.delete(f"/api/v1/companies/{admin['a']}/members/{person_id}", headers=_auth(admin["token"]))
        assert again.status_code == 404

        signed_up = _signup(lc_client, lc_app, "0620350088", "Someone Else")
        assert _companies_of(lc_client, signed_up["access_token"]) == {}

    def test_readding_a_cancelled_invitation_makes_it_pending_again(self, lc_client, lc_app, admin):
        def add():
            return lc_client.post(
                f"/api/v1/companies/{admin['a']}/members",
                json={"phone": "0620350087", "name": "Back Again"},
                headers=_auth(admin["token"]),
            )

        person_id = add().get_json()["person_id"]
        assert (
            lc_client.delete(
                f"/api/v1/companies/{admin['a']}/members/{person_id}", headers=_auth(admin["token"])
            ).status_code
            == 204
        )
        assert add().status_code == 201

        from app import db

        db.session.rollback()
        directory = lc_client.get(f"/api/v1/companies/{admin['a']}/persons", headers=_auth(admin["token"])).get_json()
        entry = next(e for e in directory["items"] if e["person_id"] == person_id)
        assert entry["is_active"] is True and entry["pending"] is True

        signed_up = _signup(lc_client, lc_app, "0620350087", "Back Again")
        assert set(_companies_of(lc_client, signed_up["access_token"])) == {str(admin["a"])}

    def test_a_member_with_an_account_is_not_cancelled_here(self, lc_client, lc_app, admin):
        signed_up = _signup(lc_client, lc_app, "0620350086", "Has Account")
        added = lc_client.post(
            f"/api/v1/companies/{admin['a']}/members", json={"phone": "0620350086"}, headers=_auth(admin["token"])
        )
        assert added.status_code == 201
        resp = lc_client.delete(
            f"/api/v1/companies/{admin['a']}/members/{added.get_json()['person_id']}", headers=_auth(admin["token"])
        )
        assert resp.status_code == 409
        assert set(_companies_of(lc_client, signed_up["access_token"])) == {str(admin["a"])}

    def test_only_a_company_admin_cancels(self, lc_client, lc_app, admin):
        added = lc_client.post(
            f"/api/v1/companies/{admin['a']}/members", json={"phone": "0620350085"}, headers=_auth(admin["token"])
        )
        person_id = added.get_json()["person_id"]
        manager_id = _make_user(lc_app, f"manager-{uuid4().hex[:8]}@test.com")
        _attach(lc_app, manager_id, admin["a"], role="manager", is_primary=True)
        from app import db

        with lc_app.app_context():
            manager_token = mint_access_token(lc_client, db.session.get(UserModel, manager_id).email)
        resp = lc_client.delete(f"/api/v1/companies/{admin['a']}/members/{person_id}", headers=_auth(manager_token))
        assert resp.status_code == 403
        # Another company's admin cannot reach this company's people either.
        other = lc_client.delete(f"/api/v1/companies/{admin['c']}/members/{person_id}", headers=_auth(admin["token"]))
        assert other.status_code == 404


class TestImportPendingPerson:
    def test_imported_pending_person_joins_both_companies_on_signup(self, lc_client, lc_app, admin):
        added = lc_client.post(
            f"/api/v1/companies/{admin['c']}/members",
            json={"phone": "0620350099", "name": "Pending Papa", "role": "manager"},
            headers=_auth(admin["token"]),
        )
        assert added.status_code == 201
        person_id = added.get_json()["person_id"]

        imported = lc_client.post(
            f"/api/v1/companies/{admin['a']}/members/import",
            json={"from_company_id": str(admin["c"]), "person_ids": [person_id]},
            headers=_auth(admin["token"]),
        )
        assert imported.status_code == 201, imported.get_data(as_text=True)
        assert [i["person_id"] for i in imported.get_json()["items"]] == [person_id]

        from app import db

        db.session.rollback()
        directory = lc_client.get(f"/api/v1/companies/{admin['a']}/persons", headers=_auth(admin["token"])).get_json()
        entry = next(e for e in directory["items"] if e["person_id"] == person_id)
        assert entry["pending"] is True
        with lc_app.app_context():
            source = (
                db.session.query(CompanyPersonModel).filter_by(company_id=admin["c"], person_id=UUID(person_id)).one()
            )
            target = (
                db.session.query(CompanyPersonModel).filter_by(company_id=admin["a"], person_id=UUID(person_id)).one()
            )
            # Same window as the source invitation; imports attach as plain members.
            assert target.pending_expires_at == source.pending_expires_at
            assert target.pending_company_role is None

        signed_up = _signup(lc_client, lc_app, "0620350099", "Papa")
        companies = _companies_of(lc_client, signed_up["access_token"])
        assert companies[str(admin["c"])]["role"] == "manager"
        assert companies[str(admin["a"])]["role"] == "member"

    def test_expired_source_invitation_gets_a_fresh_window(self, lc_client, lc_app, admin):
        added = lc_client.post(
            f"/api/v1/companies/{admin['c']}/members", json={"phone": "0620350098"}, headers=_auth(admin["token"])
        )
        person_id = added.get_json()["person_id"]
        from app import db

        with lc_app.app_context():
            row = db.session.query(CompanyPersonModel).filter_by(company_id=admin["c"], person_id=UUID(person_id)).one()
            row.pending_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.session.commit()

        imported = lc_client.post(
            f"/api/v1/companies/{admin['a']}/members/import",
            json={"from_company_id": str(admin["c"]), "person_ids": [person_id]},
            headers=_auth(admin["token"]),
        )
        assert imported.status_code == 201
        db.session.rollback()
        directory = lc_client.get(f"/api/v1/companies/{admin['a']}/persons", headers=_auth(admin["token"])).get_json()
        assert next(e for e in directory["items"] if e["person_id"] == person_id)["pending"] is True


class TestDeleteCompanyRepromotesPrimary:
    def test_members_of_a_deleted_primary_company_get_another_primary(self, lc_client, lc_app):
        ops_id = _make_user(lc_app, f"ops-{uuid4().hex[:8]}@test.com", platform_ops=True)
        charlie_id = _make_user(lc_app, f"charlie-{uuid4().hex[:8]}@test.com")
        company_a = _make_company(lc_app, "LC Keep A")
        company_d = _make_company(lc_app, "LC Keep D")
        company_b = _make_company(lc_app, "LC Doomed B")
        now = datetime.now(timezone.utc)
        _attach(lc_app, charlie_id, company_d, role="member", is_primary=False, attached_at=now)
        _attach(lc_app, charlie_id, company_a, role="member", is_primary=False, attached_at=now - timedelta(days=2))
        _attach(lc_app, charlie_id, company_b, role="admin", is_primary=True)

        from app import db

        with lc_app.app_context():
            ops_token = mint_access_token(lc_client, db.session.get(UserModel, ops_id).email)
            charlie_token = mint_access_token(lc_client, db.session.get(UserModel, charlie_id).email)

        assert lc_client.delete(f"/api/v1/companies/{company_b}", headers=_auth(ops_token)).status_code == 204

        db.session.rollback()
        companies = _companies_of(lc_client, charlie_token)
        assert set(companies) == {str(company_a), str(company_d)}
        # The earliest-attached remaining company, as boot and detach choose.
        assert companies[str(company_a)]["is_primary"] is True
        assert companies[str(company_d)]["is_primary"] is False
        products = lc_client.get("/api/v1/bibliotheque/products", headers=_auth(charlie_token))
        assert products.status_code == 200, products.get_data(as_text=True)
