"""API tests for boot/detach cleanup + join code rotation (Phase 2 onboarding, finding 11):

- Booting (or self-detaching) a company member removes their project
  assignments in that company, deactivates their directory profile, deletes
  their D8 grants in that company, and rotates the join code so the old one
  can no longer be used.
- `POST /companies/join` creates an active, linked `company_persons` row
  for the joiner if one is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token

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
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def boot_client(boot_app):
    return boot_app.test_client()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    return mint_access_token(client, email)


def _make_user(app, email: str) -> str:
    from app import db

    with app.app_context():
        user = UserModel(id=uuid4(), email=email, is_active=True)
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
        db.session.execute(
            text(
                "INSERT INTO user_projects (user_id, project_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, NULL, :at)"
            ),
            {"uid": str(user_id), "pid": str(project.id), "at": now},
        )
        db.session.commit()
        return person.id, project.id


def _grant(app, user_id, company_id, permission: str = "project:manage_labor"):
    """Give `user_id` a company-wide D8 grant in `company_id`."""
    from app import db

    with app.app_context():
        db.session.add(
            CompanyMemberGrantModel(
                id=uuid4(),
                company_id=company_id,
                user_id=user_id,
                permission=permission,
                effect="grant",
                project_id=None,
                granted_by_user_id=None,
                granted_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()


def _grant_count(app, user_id, company_id) -> int:
    from app import db

    with app.app_context():
        return db.session.query(CompanyMemberGrantModel).filter_by(user_id=user_id, company_id=company_id).count()


class TestBootCleanup:
    def test_boot_removes_assignment_deactivates_profile_and_rotates_code(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin1@test.com")
        target_id = _make_user(boot_app, "boot_target1@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Co 1", join_code="OLDCODE1")
        person_id, project_id = _attach_with_person_and_project(boot_app, target_id, company_id)
        _grant(boot_app, target_id, company_id)
        # A grant in another company the target still belongs to is not touched.
        other_company_id = _make_company(boot_app, admin_id, name="Boot Co 1 bis")
        from app import db

        with boot_app.app_context():
            db.session.add(
                UserCompanyAccessModel(
                    user_id=target_id,
                    company_id=other_company_id,
                    role="member",
                    is_primary=False,
                    attached_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()
        _grant(boot_app, target_id, other_company_id)
        token = _login(boot_client, "boot_admin1@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access/{target_id}", headers=_auth(token))
        assert resp.status_code == 204

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

        # A stale grant would outlive the role with no admin route left to revoke it.
        assert _grant_count(boot_app, target_id, company_id) == 0, "grants in the company must be deleted"
        assert _grant_count(boot_app, target_id, other_company_id) == 1

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
        _grant(boot_app, target_id, company_id)
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

        assert _grant_count(boot_app, target_id, company_id) == 0


def _task_assigned_to(app, user_id, project_id):
    from app import db
    from app.infrastructure.database.models.task import TaskModel

    with app.app_context():
        task = TaskModel(id=uuid4(), project_id=project_id, title="Poser les cloisons", assignee_id=user_id)
        db.session.add(task)
        db.session.commit()
        return task.id


def _assignee_of(app, task_id):
    from app import db
    from app.infrastructure.database.models.task import TaskModel

    with app.app_context():
        db.session.expire_all()
        return db.session.get(TaskModel, task_id).assignee_id


class TestTasksUnassignedOnLeave:
    """Whoever leaves a company is no longer named (nor pushed to) on its tasks."""

    def test_boot_clears_the_targets_tasks(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin_tasks@test.com")
        target_id = _make_user(boot_app, "boot_target_tasks@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Tasks Co")
        _person_id, project_id = _attach_with_person_and_project(boot_app, target_id, company_id)
        task_id = _task_assigned_to(boot_app, target_id, project_id)
        token = _login(boot_client, "boot_admin_tasks@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access/{target_id}", headers=_auth(token))
        assert resp.status_code == 204
        assert _assignee_of(boot_app, task_id) is None

    def test_self_detach_clears_their_tasks(self, boot_client, boot_app):
        admin_id = _make_user(boot_app, "boot_admin_tasks2@test.com")
        target_id = _make_user(boot_app, "boot_target_tasks2@test.com")
        company_id = _make_company(boot_app, admin_id, name="Boot Tasks Co 2")
        _person_id, project_id = _attach_with_person_and_project(boot_app, target_id, company_id)
        task_id = _task_assigned_to(boot_app, target_id, project_id)
        token = _login(boot_client, "boot_target_tasks2@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access", headers=_auth(token))
        assert resp.status_code == 204
        assert _assignee_of(boot_app, task_id) is None


def _assign(app, user_id, project_id):
    from app import db
    from sqlalchemy import text

    with app.app_context():
        db.session.execute(
            text(
                "INSERT INTO user_projects (user_id, project_id, invited_by_user_id, assigned_at) "
                "VALUES (:uid, :pid, NULL, :at)"
            ),
            {"uid": str(user_id), "pid": str(project_id), "at": datetime.now(timezone.utc)},
        )
        db.session.commit()


def _is_assigned(app, user_id, project_id) -> bool:
    from app import db
    from sqlalchemy import text

    with app.app_context():
        return (
            db.session.execute(
                text("SELECT 1 FROM user_projects WHERE user_id=:u AND project_id=:p"),
                {"u": str(user_id), "p": str(project_id)},
            ).fetchone()
            is not None
        )


def _attach(app, user_id, company_id, role: str, *, primary: bool = False):
    from app import db

    with app.app_context():
        db.session.add(
            UserCompanyAccessModel(
                user_id=user_id,
                company_id=company_id,
                role=role,
                is_primary=primary,
                attached_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()


def _project(app, owner_id, company_id):
    from app import db

    with app.app_context():
        project = ProjectModel(id=uuid4(), name="Demotion Project", owner_id=owner_id, company_id=company_id)
        db.session.add(project)
        db.session.commit()
        return project.id


class TestDemotedAdminLosesProjectAssignments:
    """Demoting an admin drops the project rows they got as an admin, in that company only."""

    def _set_role(self, client, token, company_id, user_id, role):
        return client.patch(
            f"/api/v1/companies/{company_id}/access/{user_id}/role", json={"role": role}, headers=_auth(token)
        )

    @pytest.mark.parametrize("new_role", ["manager", "member"])
    def test_demotion_removes_assignments_on_this_companys_projects_only(self, boot_client, boot_app, new_role):
        owner_id = _make_user(boot_app, f"demote_owner_{new_role}@test.com")
        other_admin_id = _make_user(boot_app, f"demote_admin_{new_role}@test.com")
        manager_id = _make_user(boot_app, f"demote_manager_{new_role}@test.com")
        company_id = _make_company(boot_app, owner_id, name=f"Demote Co {new_role}")
        _attach(boot_app, other_admin_id, company_id, "admin")
        _attach(boot_app, manager_id, company_id, "manager")
        project_one = _project(boot_app, owner_id, company_id)
        project_two = _project(boot_app, owner_id, company_id)
        other_company_id = _make_company(boot_app, owner_id, name=f"Demote Other {new_role}")
        _attach(boot_app, other_admin_id, other_company_id, "admin")
        other_project = _project(boot_app, owner_id, other_company_id)
        task_id = _task_assigned_to(boot_app, other_admin_id, project_one)
        for project_id in (project_one, project_two, other_project):
            _assign(boot_app, other_admin_id, project_id)
        _assign(boot_app, manager_id, project_one)
        token = _login(boot_client, f"demote_owner_{new_role}@test.com")

        resp = self._set_role(boot_client, token, company_id, other_admin_id, new_role)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        assert not _is_assigned(boot_app, other_admin_id, project_one)
        assert not _is_assigned(boot_app, other_admin_id, project_two)
        assert _assignee_of(boot_app, task_id) is None
        # Another company's rows and an unrelated member's rows stay.
        assert _is_assigned(boot_app, other_admin_id, other_project)
        assert _is_assigned(boot_app, manager_id, project_one)

    def test_other_role_changes_keep_assignments(self, boot_client, boot_app):
        owner_id = _make_user(boot_app, "demote_owner_keep@test.com")
        member_id = _make_user(boot_app, "demote_member_keep@test.com")
        company_id = _make_company(boot_app, owner_id, name="Demote Keep Co")
        _attach(boot_app, member_id, company_id, "member")
        project_id = _project(boot_app, owner_id, company_id)
        _assign(boot_app, member_id, project_id)
        token = _login(boot_client, "demote_owner_keep@test.com")

        for role in ("manager", "member", "admin"):
            resp = self._set_role(boot_client, token, company_id, member_id, role)
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert _is_assigned(boot_app, member_id, project_id), role

    def test_last_admin_demotion_is_refused_and_keeps_rows(self, boot_client, boot_app):
        owner_id = _make_user(boot_app, "demote_owner_last@test.com")
        company_id = _make_company(boot_app, owner_id, name="Demote Last Co")
        project_id = _project(boot_app, owner_id, company_id)
        _assign(boot_app, owner_id, project_id)
        token = _login(boot_client, "demote_owner_last@test.com")

        resp = self._set_role(boot_client, token, company_id, owner_id, "member")
        assert resp.status_code == 409
        assert _is_assigned(boot_app, owner_id, project_id)


def _worker_linked_to(app, user_id, project_id):
    from decimal import Decimal

    from app import db
    from app.infrastructure.database.models import WorkerModel

    with app.app_context():
        worker = WorkerModel(
            id=uuid4(), project_id=project_id, name="Linked Worker", daily_rate=Decimal("100.00"), user_id=user_id
        )
        db.session.add(worker)
        db.session.commit()
        return worker.id


def _worker_account(app, worker_id):
    from app import db
    from app.infrastructure.database.models import WorkerModel

    with app.app_context():
        db.session.expire_all()
        return db.session.get(WorkerModel, worker_id).user_id


class TestWorkersUnlinkedOnLeave:
    """Whoever leaves a company no longer has a worker row of its projects tied to their account."""

    def _setup(self, app, tag):
        admin_id = _make_user(app, f"boot_admin_{tag}@test.com")
        target_id = _make_user(app, f"boot_target_{tag}@test.com")
        company_id = _make_company(app, admin_id, name=f"Unlink Co {tag}")
        _person_id, project_id = _attach_with_person_and_project(app, target_id, company_id)
        other_company_id = _make_company(app, admin_id, name=f"Unlink Other {tag}")
        _attach(app, target_id, other_company_id, "member")
        other_project_id = _project(app, admin_id, other_company_id)
        return (
            admin_id,
            target_id,
            company_id,
            _worker_linked_to(app, target_id, project_id),
            _worker_linked_to(app, target_id, other_project_id),
        )

    def test_boot_unlinks_the_targets_workers_in_that_company_only(self, boot_client, boot_app):
        _admin, target_id, company_id, worker_id, other_worker_id = self._setup(boot_app, "wk1")
        token = _login(boot_client, "boot_admin_wk1@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access/{target_id}", headers=_auth(token))
        assert resp.status_code == 204
        assert _worker_account(boot_app, worker_id) is None
        assert _worker_account(boot_app, other_worker_id) == target_id

    def test_self_detach_unlinks_their_workers_in_that_company_only(self, boot_client, boot_app):
        _admin, target_id, company_id, worker_id, other_worker_id = self._setup(boot_app, "wk2")
        token = _login(boot_client, "boot_target_wk2@test.com")

        resp = boot_client.delete(f"/api/v1/companies/{company_id}/access", headers=_auth(token))
        assert resp.status_code == 204
        assert _worker_account(boot_app, worker_id) is None
        assert _worker_account(boot_app, other_worker_id) == target_id


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


class TestJoinCodeRejectsMalformedBody:
    """A bad body must come back as a 422 validation error, never a 500.

    `format_validation_error` returns a ready `(response, status)` pair; feeding it to a
    helper that expects a message string embeds a Flask Response in the JSON payload and
    makes `jsonify` raise, turning every client mistake into a server error.
    """

    @pytest.mark.parametrize(
        "body",
        [
            {"join_code": "JOINME4"},  # wrong field name — the schema forbids extras
            {},  # missing `code`
            {"code": "x"},  # shorter than min_length
        ],
    )
    def test_malformed_body_returns_422(self, boot_client, boot_app, body):
        _make_user(boot_app, f"boot_bad_{abs(hash(str(body)))}@test.com")
        token = _login(boot_client, f"boot_bad_{abs(hash(str(body)))}@test.com")

        resp = boot_client.post("/api/v1/companies/join", json=body, headers=_auth(token))

        assert resp.status_code == 422, resp.get_data(as_text=True)
        payload = resp.get_json()
        assert payload["error"] == "validation_error"
        assert isinstance(payload["message"], str) and payload["message"]
