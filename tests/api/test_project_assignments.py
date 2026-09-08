"""API tests for project assignment endpoints (Phase 2 onboarding slice B):

  PUT    /projects/<id>/assignments/<user_id>
  DELETE /projects/<id>/assignments/<user_id>

Assignment targets an EXISTING company member (distinct from invitations,
the outsider path) — a stranger to the company gets 404, not 403.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def assign_app():
    from app import create_app, db
    from config import TestingConfig

    class AssignTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AssignTestConfig)
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def assign_client(assign_app):
    return assign_app.test_client()


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


def _attach(app, user_id, company_id, role: str) -> None:
    from app import db

    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id,
                company_id=company_id,
                role=role,
                is_primary=True,
                attached_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()


def _make_company_and_project(app, admin_id) -> tuple:
    from app import db

    now = datetime.now(timezone.utc)
    with app.app_context():
        company = CompanyModel(
            id=uuid4(), legal_name="Assign Co", address="1 rue", created_by=admin_id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.flush()
        db.session.add(
            UserCompanyAccessModel(
                user_id=admin_id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        project = ProjectModel(id=uuid4(), name="Assign Project", owner_id=admin_id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return company.id, project.id


class TestAdminAssigns:
    def test_admin_assigns_any_company_member(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin1@test.com")
        target_id = _make_user(assign_app, "asg_target1@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, target_id, company_id, "member")
        token = _login(assign_client, "asg_admin1@test.com")

        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

        from app import db
        from sqlalchemy import text

        with assign_app.app_context():
            row = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(target_id), "p": str(project_id)},
            ).fetchone()
            assert row is not None

    def test_target_not_company_member_is_404(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin2@test.com")
        stranger_id = _make_user(assign_app, "asg_stranger2@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        token = _login(assign_client, "asg_admin2@test.com")

        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{stranger_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 404


class TestManagerAssigns:
    def test_manager_assigned_to_project_can_assign_a_company_member(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin3@test.com")
        manager_id = _make_user(assign_app, "asg_manager3@test.com")
        target_id = _make_user(assign_app, "asg_target3@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, manager_id, company_id, "manager")
        _attach(assign_app, target_id, company_id, "member")

        # Assign the manager to the project first (as admin).
        admin_token = _login(assign_client, "asg_admin3@test.com")
        assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{manager_id}",
            headers=_auth(admin_token),
        )

        manager_token = _login(assign_client, "asg_manager3@test.com")
        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(manager_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_manager_cannot_assign_another_manager(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin4@test.com")
        manager_id = _make_user(assign_app, "asg_manager4@test.com")
        target_id = _make_user(assign_app, "asg_target4@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, manager_id, company_id, "manager")
        _attach(assign_app, target_id, company_id, "manager")

        admin_token = _login(assign_client, "asg_admin4@test.com")
        assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{manager_id}",
            headers=_auth(admin_token),
        )

        manager_token = _login(assign_client, "asg_manager4@test.com")
        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(manager_token),
        )
        assert resp.status_code == 403

    def test_manager_not_assigned_to_project_is_403(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin5@test.com")
        manager_id = _make_user(assign_app, "asg_manager5@test.com")
        target_id = _make_user(assign_app, "asg_target5@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, manager_id, company_id, "manager")
        _attach(assign_app, target_id, company_id, "member")

        manager_token = _login(assign_client, "asg_manager5@test.com")
        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(manager_token),
        )
        assert resp.status_code == 403


class TestUnassign:
    def test_admin_unassigns_member(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin6@test.com")
        target_id = _make_user(assign_app, "asg_target6@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, target_id, company_id, "member")
        token = _login(assign_client, "asg_admin6@test.com")

        put_resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(token),
        )
        assert put_resp.status_code == 200

        del_resp = assign_client.delete(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            headers=_auth(token),
        )
        assert del_resp.status_code == 204

        from app import db
        from sqlalchemy import text

        with assign_app.app_context():
            row = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(target_id), "p": str(project_id)},
            ).fetchone()
            assert row is None

    def test_unassigning_a_stranger_answers_404_like_assigning_does(self, assign_client, assign_app):
        """Same state, same answer for both callers and both verbs."""
        admin_id = _make_user(assign_app, "asg_admin7@test.com")
        manager_id = _make_user(assign_app, "asg_manager7@test.com")
        stranger_id = _make_user(assign_app, "asg_stranger7@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, manager_id, company_id, "manager")
        admin_token = _login(assign_client, "asg_admin7@test.com")
        assign_client.put(f"/api/v1/projects/{project_id}/assignments/{manager_id}", headers=_auth(admin_token))

        for token in (admin_token, _login(assign_client, "asg_manager7@test.com")):
            resp = assign_client.delete(
                f"/api/v1/projects/{project_id}/assignments/{stranger_id}",
                headers=_auth(token),
            )
            assert resp.status_code == 404, resp.get_data(as_text=True)


class TestAssignWithRole:
    def _assignment_exists(self, app, user_id, project_id) -> bool:
        from sqlalchemy import text

        from app import db

        with app.app_context():
            row = db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(user_id), "p": str(project_id)},
            ).fetchone()
            return row is not None

    def _company_role(self, app, user_id, company_id):
        from app import db
        from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

        with app.app_context():
            db.session.expire_all()
            return db.session.get(UserCompanyAccessModel, (user_id, company_id)).role

    def test_admin_assigning_as_manager_raises_the_company_role(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin_r1@test.com")
        target_id = _make_user(assign_app, "asg_target_r1@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, target_id, company_id, "member")
        token = _login(assign_client, "asg_admin_r1@test.com")

        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            json={"role": "manager"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["role"] == "manager"
        assert self._company_role(assign_app, target_id, company_id) == "manager"

    def test_role_member_never_demotes_and_is_echoed(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin_r2@test.com")
        target_id = _make_user(assign_app, "asg_target_r2@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, target_id, company_id, "manager")
        token = _login(assign_client, "asg_admin_r2@test.com")

        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            json={"role": "member"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["role"] == "manager"
        assert self._company_role(assign_app, target_id, company_id) == "manager"

    def test_manager_cannot_assign_as_manager(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin_r3@test.com")
        manager_id = _make_user(assign_app, "asg_manager_r3@test.com")
        target_id = _make_user(assign_app, "asg_target_r3@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, manager_id, company_id, "manager")
        _attach(assign_app, target_id, company_id, "member")
        admin_token = _login(assign_client, "asg_admin_r3@test.com")
        assign_client.put(f"/api/v1/projects/{project_id}/assignments/{manager_id}", headers=_auth(admin_token))
        token = _login(assign_client, "asg_manager_r3@test.com")

        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            json={"role": "manager"},
            headers=_auth(token),
        )
        assert resp.status_code == 403
        assert self._company_role(assign_app, target_id, company_id) == "member"
        # The refused promotion rolls the assignment back too — a manager
        # cannot use the old payload to assign someone as a side effect.
        assert not self._assignment_exists(assign_app, target_id, project_id)

    def test_invalid_role_is_rejected(self, assign_client, assign_app):
        admin_id = _make_user(assign_app, "asg_admin_r4@test.com")
        target_id = _make_user(assign_app, "asg_target_r4@test.com")
        company_id, project_id = _make_company_and_project(assign_app, admin_id)
        _attach(assign_app, target_id, company_id, "member")
        token = _login(assign_client, "asg_admin_r4@test.com")
        resp = assign_client.put(
            f"/api/v1/projects/{project_id}/assignments/{target_id}",
            json={"role": "owner"},
            headers=_auth(token),
        )
        # 422 is the repo-wide body-validation answer (format_validation_error).
        assert resp.status_code == 422
        assert not self._assignment_exists(assign_app, target_id, project_id)
