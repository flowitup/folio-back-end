"""API tests for POST /companies/<id>/access/<user_id> (admin attaches an existing
user account to a company) and the `companies` field on
GET /companies/<id>/attached-users (D4: scoped to the companies the caller admins).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


@pytest.fixture(scope="module")
def atc_app():
    """Flask app with two companies (A, B), each with its own admin, plus a plain member of A."""
    from app import create_app, db
    from config import TestingConfig

    class AtcTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AtcTestConfig)

    with test_app.app_context():
        db.create_all()

        def _user(email: str) -> UserModel:
            u = UserModel(id=uuid4(), email=email, is_active=True)
            db.session.add(u)
            return u

        company_a_admin = _user("atc_company_a_admin@test.com")
        company_b_admin = _user("atc_company_b_admin@test.com")
        plain_member = _user("atc_plain_member@test.com")
        db.session.flush()

        now = datetime.now(timezone.utc)

        def _company(name: str, admin_id: UUID) -> CompanyModel:
            c = CompanyModel(
                id=uuid4(),
                legal_name=name,
                address="1 rue de la Paix",
                created_by=admin_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(c)
            return c

        company_a = _company("Attach Co A", company_a_admin.id)
        company_b = _company("Attach Co B", company_b_admin.id)
        db.session.flush()

        def _access(user_id: UUID, company_id: UUID, role: str, is_primary: bool = True) -> None:
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user_id,
                    company_id=company_id,
                    role=role,
                    is_primary=is_primary,
                    attached_at=now,
                )
            )

        _access(company_a_admin.id, company_a.id, "admin")
        _access(company_b_admin.id, company_b.id, "admin")
        _access(plain_member.id, company_a.id, "member")

        db.session.commit()

        test_app._test_company_a_admin_email = "atc_company_a_admin@test.com"
        test_app._test_company_b_admin_email = "atc_company_b_admin@test.com"
        test_app._test_member_email = "atc_plain_member@test.com"
        test_app._test_company_a_id = str(company_a.id)
        test_app._test_company_b_id = str(company_b.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def atc_client(atc_app):
    return atc_app.test_client()


@pytest.fixture
def company_a_admin_token(atc_client, atc_app):
    return _login(atc_client, atc_app._test_company_a_admin_email)


@pytest.fixture
def company_b_admin_token(atc_client, atc_app):
    return _login(atc_client, atc_app._test_company_b_admin_email)


@pytest.fixture
def member_token(atc_client, atc_app):
    return _login(atc_client, atc_app._test_member_email)


def _make_user(app) -> str:
    """A fresh account attached to no company — the usual attach target."""
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=f"atc_target_{uuid4().hex[:8]}@test.com", is_active=True)
        db.session.add(user)
        db.session.commit()
        return str(user.id)


def _user_id_by_email(app, email: str) -> str:
    from app import db

    with app.app_context():
        user = db.session.query(UserModel).filter_by(email=email).one()
        return str(user.id)


def _company_person_row(app, company_id: str, user_id: str):
    """The active `company_persons` row linking `user_id` to `company_id`, or None."""
    from app import db

    with app.app_context():
        person = db.session.query(PersonModel).filter_by(user_id=UUID(user_id)).one_or_none()
        if person is None:
            return None
        return (
            db.session.query(CompanyPersonModel)
            .filter_by(company_id=UUID(company_id), person_id=person.id)
            .one_or_none()
        )


class TestAttachUserToCompany:
    def test_attach_creates_member_access_row(self, atc_client, atc_app, company_a_admin_token):
        target_id = _make_user(atc_app)

        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["user_id"] == target_id
        assert body["company_id"] == atc_app._test_company_a_id
        assert body["role"] == "member"

    def test_attach_is_primary_true_on_first_attachment(self, atc_client, atc_app, company_a_admin_token):
        target_id = _make_user(atc_app)

        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )

        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["is_primary"] is True

    def test_attach_is_primary_false_when_user_already_has_a_company(self, atc_client, atc_app, company_a_admin_token):
        # company_b_admin already carries company B as their primary company.
        target_id = _user_id_by_email(atc_app, atc_app._test_company_b_admin_email)

        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )

        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["is_primary"] is False

    def test_attach_creates_active_company_person_row(self, atc_client, atc_app, company_a_admin_token):
        target_id = _make_user(atc_app)

        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

        row = _company_person_row(atc_app, atc_app._test_company_a_id, target_id)
        assert row is not None and row.is_active is True

    def test_attach_idempotent_no_duplicate_row_and_role_unchanged(self, atc_client, atc_app, company_a_admin_token):
        target_id = _make_user(atc_app)
        company_id = atc_app._test_company_a_id

        first = atc_client.post(
            f"/api/v1/companies/{company_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )
        assert first.status_code == 200, first.get_data(as_text=True)

        # Promote to admin directly so the second attach call's response can
        # prove it did NOT reset the role back to "member".
        from app import db

        with atc_app.app_context():
            row = db.session.get(UserCompanyAccessModel, (UUID(target_id), UUID(company_id)))
            row.role = "admin"
            db.session.commit()

        second = atc_client.post(
            f"/api/v1/companies/{company_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )
        assert second.status_code == 200, second.get_data(as_text=True)
        assert second.get_json()["role"] == "admin"

        with atc_app.app_context():
            count = (
                db.session.query(UserCompanyAccessModel)
                .filter_by(user_id=UUID(target_id), company_id=UUID(company_id))
                .count()
            )
            assert count == 1

    def test_non_admin_caller_gets_403(self, atc_client, atc_app, member_token):
        target_id = _make_user(atc_app)

        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(member_token),
        )

        assert resp.status_code == 403

    def test_unknown_target_user_returns_404(self, atc_client, atc_app, company_a_admin_token):
        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{uuid4()}",
            headers=_auth(company_a_admin_token),
        )

        assert resp.status_code == 404


class TestAttachedUsersCompaniesField:
    def test_companies_field_excludes_company_caller_does_not_administer(
        self, atc_client, atc_app, company_a_admin_token, company_b_admin_token
    ):
        # One user attached to BOTH company A and company B.
        target_id = _make_user(atc_app)

        resp_a = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )
        assert resp_a.status_code == 200, resp_a.get_data(as_text=True)

        resp_b = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_b_id}/access/{target_id}",
            headers=_auth(company_b_admin_token),
        )
        assert resp_b.status_code == 200, resp_b.get_data(as_text=True)

        # company_a_admin administers company A only: the target's `companies`
        # list on company A's roster must show A alone, never B (D4).
        listing = atc_client.get(
            f"/api/v1/companies/{atc_app._test_company_a_id}/attached-users",
            headers=_auth(company_a_admin_token),
        )
        assert listing.status_code == 200, listing.get_data(as_text=True)

        rows = {row["user_id"]: row for row in listing.get_json()["items"]}
        target_row = rows[target_id]
        company_ids_shown = {c["id"] for c in target_row["companies"]}

        assert company_ids_shown == {atc_app._test_company_a_id}
        assert atc_app._test_company_b_id not in company_ids_shown


class TestAttachedUsersAssignedProjects:
    def test_assignments_reported_for_an_account_absent_from_the_directory(
        self, atc_client, atc_app, company_a_admin_token
    ):
        """A user attached without a `company_persons` profile still reports
        their project assignments.

        The directory is the only other source of `assigned_project_ids`, and
        it lists nobody who lacks a profile — every pre-Phase-2 account. A
        member list sourcing assignments from the directory alone would show
        such a user as unassigned even while they hold the assignment.
        """
        from sqlalchemy import text

        from app import db
        from app.infrastructure.database.models.project import ProjectModel

        target_id = _make_user(atc_app)
        resp = atc_client.post(
            f"/api/v1/companies/{atc_app._test_company_a_id}/access/{target_id}",
            headers=_auth(company_a_admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with atc_app.app_context():
            owner_id = _user_id_by_email(atc_app, atc_app._test_company_a_admin_email)
            project = ProjectModel(
                id=uuid4(),
                name="ATC assignment project",
                owner_id=UUID(owner_id),
                company_id=UUID(atc_app._test_company_a_id),
            )
            db.session.add(project)
            db.session.flush()
            # user_projects is an association table with no ORM model.
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, invited_by_user_id, assigned_at) "
                    "VALUES (:uid, :pid, NULL, :at)"
                ),
                {"uid": target_id, "pid": str(project.id), "at": datetime.now(timezone.utc)},
            )
            db.session.commit()
            project_id = str(project.id)

        listing = atc_client.get(
            f"/api/v1/companies/{atc_app._test_company_a_id}/attached-users",
            headers=_auth(company_a_admin_token),
        )
        assert listing.status_code == 200, listing.get_data(as_text=True)

        row = {r["user_id"]: r for r in listing.get_json()["items"]}[target_id]
        assert row["assigned_project_ids"] == [project_id]
