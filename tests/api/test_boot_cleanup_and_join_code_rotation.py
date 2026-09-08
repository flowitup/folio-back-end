"""API tests for boot/detach cleanup + join code rotation (Phase 2 onboarding, finding 11):

- Booting (or self-detaching) a company member removes their project
  assignments in that company, deactivates their directory profile, and
  rotates the join code so the old one can no longer be used.
- `POST /companies/join` creates an active, linked `company_persons` row
  for the joiner if one is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.role import RoleModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def boot_app():
    from app import create_app, db
    from config import TestingConfig

    class BootTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(BootTestConfig)
    with test_app.app_context():
        db.create_all()
        db.session.add(RoleModel(name="member", description="Member"))
        db.session.commit()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def boot_client(boot_app):
    return boot_app.test_client()


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


def _make_company(app, admin_id, *, name: str, join_code: str | None = None):
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(),
            legal_name=name,
            address="1 rue",
            created_by=admin_id,
            created_at=now,
            updated_at=now,
            join_code=join_code,
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        db.session.commit()
        return company.id


def _attach_with_person_and_project(app, user_id, company_id):
    """Attach `user_id` as a member, give them a Person + active company_persons
    row, and assign them to a project of this company — so we can assert all
    three get cleaned up on boot/detach."""
    from app import db
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id, company_id=company_id, role="member", is_primary=True, attached_at=now
            )
        )
        person = PersonModel(
            id=uuid4(),
            name="Cleanup Target",
            normalized_name="cleanup target",
            created_by_user_id=user_id,
            created_at=now,
            user_id=user_id,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            CompanyPersonModel(id=uuid4(), company_id=company_id, person_id=person.id, is_active=True, created_at=now)
        )
        project = ProjectModel(id=uuid4(), name="Cleanup Project", owner_id=user_id, company_id=company_id)
        db.session.add(project)
        db.session.flush()
        member_role = db.session.query(RoleModel).filter_by(name="member").first()
        db.session.execute(
            text(
                "INSERT INTO user_projects (user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, :rid, NULL, :at)"
            ),
            {"uid": str(user_id), "pid": str(project.id), "rid": str(member_role.id), "at": now},
        )
        db.session.commit()
        return person.id, project.id


class TestBootCleanup:
    def test_boot_removes_assignment_deactivates_profile_and_rotates_code(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin1@test.com")
        target_id = _make_user(boot_app, "boot_target1@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Co 1", join_code="OLDCODE1")
        person_id, project_id = _attach_with_person_and_project(boot_app, target_id, company_id)
        token = _login(boot_client, "boot_admin1@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access/{target_id}", headers=_auth(token))
        assert resp.status_code == 204

        from app import db
        from sqlalchemy import text

        with boot_app.app_context():
            row = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(target_id), "p": str(project_id)},
            ).fetchone()
            assert row is None, "assignment must be removed"

            cp_row = db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=person_id).first()
            assert cp_row is not None and cp_row.is_active is False, "profile must be deactivated"

            company = db.session.get(CompanyModel, company_id)
            assert company.join_code != "OLDCODE1", "join code must be rotated"

        # The old join code must no longer work.
        old_code_join = boot_client.post(
            "/api/v1/companies/join",
            json={"code": "OLDCODE1"},
            headers=_auth(_login(boot_client, "boot_target1@test.com")),
        )
        assert old_code_join.status_code == 404


class TestSelfDetachCleanup:
    def test_self_detach_removes_assignment_and_deactivates_profile(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin2@test.com")
        target_id = _make_user(boot_app, "boot_target2@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Co 2", join_code="OLDCODE2")
        person_id, project_id = _attach_with_person_and_project(boot_app, target_id, company_id)
        token = _login(boot_client, "boot_target2@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access", headers=_auth(token))
        assert resp.status_code == 204

        from app import db
        from sqlalchemy import text

        with boot_app.app_context():
            row = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(target_id), "p": str(project_id)},
            ).fetchone()
            assert row is None

            cp_row = db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=person_id).first()
            assert cp_row is not None and cp_row.is_active is False


class TestJoinCodeCreatesCompanyPerson:
    def test_join_by_code_creates_active_company_person(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin3@test.com")
        joiner_id = _make_user(boot_app, "boot_joiner3@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Co 3", join_code="JOINME3")
        token = _login(boot_client, "boot_joiner3@test.com")

        resp = boot_client.post("/api/v1/companies/join", json={"code": "JOINME3"}, headers=_auth(token))
        assert resp.status_code == 200, resp.get_data(as_text=True)

        from app import db

        with boot_app.app_context():
            person = db.session.query(PersonModel).filter_by(user_id=joiner_id).first()
            assert person is not None
            cp_row = db.session.query(CompanyPersonModel).filter_by(company_id=company_id, person_id=person.id).first()
            assert cp_row is not None and cp_row.is_active is True
