"""Integration tests — worker self-logged attendance → manager validation.

Covers:
  POST /projects/<id>/labor-entries/self               (201 pending, 404 unlinked, 409 dup, 400 bad row/window)
  POST /projects/<id>/labor-entries/<entry>/validate   (200 validated + idempotent, 403 for a plain member)
  POST /projects/<id>/labor-entries/<entry>/reject     (204 deletes pending, 409 once validated)
  GET  /projects/<id>/labor-entries?status=            (filter + new fields)
  GET  /projects/<id>/labor-summary                    (pending excluded until validated)
  GET  /notifications                                  (attendance_pending only for validators)
  PUT  /projects/<id>/workers/<id>                     (user_id link + uniqueness per project)
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    PermissionModel,
    ProjectModel,
    RoleModel,
    UserModel,
    WorkerModel,
)
from app.infrastructure.database.models.associations import user_projects

# Membership-role permission lookups use raw SQL with dashed UUID strings → Postgres only.
_needs_pg = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="membership-role permission union relies on Postgres uuid comparison",
)


@pytest.fixture(scope="module")
def av_app():
    """App with an owner/manager, a linked member worker and an unlinked member."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
    from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
    from app.infrastructure.adapters.sqlalchemy_pending_attendance_query import SQLAlchemyPendingAttendanceQuery
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
        SqlAlchemyProjectTagRepository,
    )
    from app.application.labor import (
        ListPendingAttendanceUseCase,
        LogAttendanceUseCase,
        RejectAttendanceUseCase,
        SubmitOwnAttendanceUseCase,
        UpdateAttendanceUseCase,
        ValidateAttendanceUseCase,
    )
    from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class AVTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(AVTestConfig)

    with test_app.app_context():
        db.create_all()
        hasher = Argon2PasswordHasher()

        manage = PermissionModel(name="project:manage_labor", resource="project", action="manage_labor")
        read = PermissionModel(name="project:read", resource="project", action="read")
        self_log = PermissionModel(name="project:log_own_attendance", resource="project", action="log_own_attendance")
        manager_role = RoleModel(name="manager", description="Manager")
        manager_role.permissions.extend([manage, read, self_log])
        member_role = RoleModel(name="member", description="Member")
        member_role.permissions.extend([read, self_log])
        db.session.add_all([manage, read, self_log, manager_role, member_role])
        db.session.flush()

        owner = UserModel(email="owner@av-test.com", password_hash=hasher.hash("Pass1234!"), is_active=True)
        owner.roles.append(manager_role)
        linked = UserModel(email="linked@av-test.com", password_hash=hasher.hash("Pass1234!"), is_active=True)
        linked.roles.append(member_role)
        unlinked = UserModel(email="unlinked@av-test.com", password_hash=hasher.hash("Pass1234!"), is_active=True)
        unlinked.roles.append(member_role)
        # Global role is the read-only default; manager rights come only from the membership role.
        chef = UserModel(email="chef@av-test.com", password_hash=hasher.hash("Pass1234!"), is_active=True)
        chef.roles.append(member_role)
        db.session.add_all([owner, linked, unlinked, chef])
        db.session.flush()

        project = ProjectModel(name="Chantier AV", owner_id=owner.id)
        other_project = ProjectModel(name="Other site", owner_id=owner.id)
        db.session.add_all([project, other_project])
        db.session.flush()
        for u in (linked, unlinked):
            db.session.execute(
                user_projects.insert().values(user_id=u.id, project_id=project.id, role_id=member_role.id)
            )
        db.session.execute(
            user_projects.insert().values(user_id=chef.id, project_id=project.id, role_id=manager_role.id)
        )

        worker = WorkerModel(project_id=project.id, name="Linked Worker", daily_rate=100, user_id=linked.id)
        free_worker = WorkerModel(project_id=project.id, name="Free Worker", daily_rate=80)
        db.session.add_all([worker, free_worker])
        db.session.commit()

        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)
        configure_container(
            user_repository=SQLAlchemyUserRepository(db.session),
            project_repository=SQLAlchemyProjectRepository(db.session),
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            worker_repository=worker_repo,
            labor_entry_repository=entry_repo,
            project_membership_repo=SqlAlchemyProjectMembershipRepository(db.session),
            role_repo=SqlAlchemyRoleRepository(db.session),
        )
        c = get_container()
        c.submit_own_attendance_usecase = SubmitOwnAttendanceUseCase(worker_repo=worker_repo, entry_repo=entry_repo)
        c.validate_attendance_usecase = ValidateAttendanceUseCase(entry_repo=entry_repo, worker_repo=worker_repo)
        c.reject_attendance_usecase = RejectAttendanceUseCase(entry_repo=entry_repo, worker_repo=worker_repo)
        c.list_pending_attendance_usecase = ListPendingAttendanceUseCase(SQLAlchemyPendingAttendanceQuery(db.session))
        tag_repo = SqlAlchemyProjectTagRepository(db.session)
        c.log_attendance_usecase = LogAttendanceUseCase(
            worker_repo=worker_repo, entry_repo=entry_repo, tag_repo=tag_repo
        )
        c.update_attendance_usecase = UpdateAttendanceUseCase(
            entry_repo=entry_repo, worker_repo=worker_repo, tag_repo=tag_repo
        )

        # The note due-reminder query is Postgres-only SQL (covered by PG-gated tests);
        # the bell test only asserts the attendance half, so feed it an empty note list.
        class _NoNotes:
            def list_due_for_user(self, user_id, now, limit=100):
                return []

        c.list_due_notifications_usecase = ListDueNotificationsUseCase(note_query=_NoNotes())

        test_app.config["_ids"] = {
            "project": str(project.id),
            "other_project": str(other_project.id),
            "worker": str(worker.id),
            "free_worker": str(free_worker.id),
            "linked": str(linked.id),
            "unlinked": str(unlinked.id),
            "owner": str(owner.id),
            "chef": str(chef.id),
        }
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(av_app):
    return av_app.test_client()


@pytest.fixture
def ids(av_app):
    return av_app.config["_ids"]


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass1234!"})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def owner_h(client):
    return _login(client, "owner@av-test.com")


@pytest.fixture
def linked_h(client):
    return _login(client, "linked@av-test.com")


@pytest.fixture
def unlinked_h(client):
    return _login(client, "unlinked@av-test.com")


@pytest.fixture
def chef_h(client):
    return _login(client, "chef@av-test.com")


@pytest.fixture(autouse=True)
def _clean_entries(av_app):
    """Each test starts with no labor entries (module-scoped app, per-test cleanup)."""
    from app import db
    from app.infrastructure.database.models import LaborEntryModel

    with av_app.app_context():
        db.session.query(LaborEntryModel).delete()
        db.session.commit()
    yield


def _self_url(ids):
    return f"/api/v1/projects/{ids['project']}/labor-entries/self"


def _submit(client, headers, ids, day=None, **body):
    payload = {"date": (day or date.today()).isoformat(), "shift_type": "full"}
    payload.update(body)
    return client.post(_self_url(ids), json=payload, headers=headers)


class TestSelfLog:
    def test_linked_worker_creates_pending_entry(self, client, linked_h, ids):
        r = _submit(client, linked_h, ids, note="  arrived 7am ")
        assert r.status_code == 201, r.get_json()
        body = r.get_json()
        assert body["status"] == "pending"
        assert body["worker_id"] == ids["worker"]
        assert body["worker_name"] == "Linked Worker"
        assert body["submitted_by_user_id"] == ids["linked"]
        assert body["note"] == "arrived 7am"

    def test_unlinked_member_gets_404(self, client, unlinked_h, ids):
        r = _submit(client, unlinked_h, ids)
        assert r.status_code == 404
        assert r.get_json()["error"] == "WorkerNotLinked"

    def test_same_day_twice_is_409(self, client, linked_h, ids):
        assert _submit(client, linked_h, ids).status_code == 201
        assert _submit(client, linked_h, ids).status_code == 409

    def test_yesterday_ok_but_two_days_back_rejected(self, client, linked_h, ids):
        assert _submit(client, linked_h, ids, day=date.today() - timedelta(days=1)).status_code == 201
        r = _submit(client, linked_h, ids, day=date.today() - timedelta(days=2))
        assert r.status_code == 400

    def test_empty_row_is_rejected(self, client, linked_h, ids):
        r = client.post(_self_url(ids), json={"date": date.today().isoformat()}, headers=linked_h)
        # Schema validation errors map to 400 ValidationError in this API (see validation_error_response).
        assert r.status_code == 400
        assert r.get_json()["error"] == "ValidationError"

    def test_manager_only_fields_are_rejected(self, client, linked_h, ids):
        r = _submit(client, linked_h, ids, amount_override=999)
        # Pydantic drops unknown fields by default; the override must never land on the row.
        assert r.status_code == 201
        listed = client.get(
            f"/api/v1/projects/{ids['project']}/labor-entries?status=pending", headers=linked_h
        ).get_json()
        assert listed["entries"][0]["amount_override"] is None

    def test_requires_auth(self, client, ids):
        assert client.post(_self_url(ids), json={"date": "2026-01-01", "shift_type": "full"}).status_code == 401


class TestValidateReject:
    def _pending_id(self, client, linked_h, ids):
        r = _submit(client, linked_h, ids)
        assert r.status_code == 201
        return r.get_json()["id"]

    def test_manager_validates_then_it_is_priced(self, client, linked_h, owner_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        base = f"/api/v1/projects/{ids['project']}"

        before = client.get(f"{base}/labor-summary", headers=owner_h).get_json()
        assert before["total_cost"] == 0

        r = client.post(f"{base}/labor-entries/{entry_id}/validate", headers=owner_h)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["status"] == "validated"
        assert r.get_json()["validated_by_user_id"] == ids["owner"]
        assert r.get_json()["validated_at"]

        after = client.get(f"{base}/labor-summary", headers=owner_h).get_json()
        assert after["total_cost"] == 100.0

        # idempotent
        again = client.post(f"{base}/labor-entries/{entry_id}/validate", headers=owner_h)
        assert again.status_code == 200
        assert again.get_json()["validated_by_user_id"] == ids["owner"]

    def test_member_cannot_validate_or_reject(self, client, linked_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        base = f"/api/v1/projects/{ids['project']}/labor-entries/{entry_id}"
        assert client.post(f"{base}/validate", headers=linked_h).status_code == 403
        assert client.post(f"{base}/reject", headers=linked_h).status_code == 403

    def test_reject_deletes_pending_row(self, client, linked_h, owner_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        base = f"/api/v1/projects/{ids['project']}"
        assert client.post(f"{base}/labor-entries/{entry_id}/reject", headers=owner_h).status_code == 204
        listed = client.get(f"{base}/labor-entries", headers=owner_h).get_json()
        assert listed["total"] == 0
        # the worker can resubmit the same day
        assert _submit(client, linked_h, ids).status_code == 201

    def test_reject_validated_is_409_and_not_found_is_404(self, client, linked_h, owner_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        base = f"/api/v1/projects/{ids['project']}/labor-entries"
        assert client.post(f"{base}/{entry_id}/validate", headers=owner_h).status_code == 200
        assert client.post(f"{base}/{entry_id}/reject", headers=owner_h).status_code == 409
        assert client.post(f"{base}/{uuid4()}/reject", headers=owner_h).status_code == 404
        assert client.post(f"{base}/{uuid4()}/validate", headers=owner_h).status_code == 404

    def test_entry_from_other_project_is_404(self, client, linked_h, owner_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        other = f"/api/v1/projects/{ids['other_project']}/labor-entries/{entry_id}"
        assert client.post(f"{other}/validate", headers=owner_h).status_code == 404
        assert client.post(f"{other}/reject", headers=owner_h).status_code == 404

    def test_membership_role_manager_sees_bell(self, client, linked_h, chef_h, ids):
        """Bell visibility via the per-project membership role, not the global role."""
        entry_id = self._pending_id(client, linked_h, ids)
        bell = client.get("/api/v1/notifications", headers=chef_h).get_json()
        assert [p["entry_id"] for p in bell["attendance_pending"]] == [entry_id]

    @_needs_pg
    def test_membership_role_manager_can_validate(self, client, linked_h, chef_h, ids):
        """The route's membership-role permission union resolves user_projects with raw SQL
        that compares dashed UUID strings — a no-match on SQLite's dash-less storage, so
        this half of the membership-role path is exercised against Postgres only."""
        entry_id = self._pending_id(client, linked_h, ids)
        r = client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry_id}/validate", headers=chef_h)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["validated_by_user_id"] == ids["chef"]

    def test_manager_edit_keeps_pending_and_pending_is_unpriced_in_list(self, client, linked_h, owner_h, ids):
        entry_id = self._pending_id(client, linked_h, ids)
        base = f"/api/v1/projects/{ids['project']}/labor-entries"
        r = client.put(f"{base}/{entry_id}", json={"note": "checked by chef"}, headers=owner_h)
        assert r.status_code == 200
        listed = client.get(base, headers=owner_h).get_json()["entries"]
        assert listed[0]["status"] == "pending"
        assert listed[0]["note"] == "checked by chef"
        assert listed[0]["effective_cost"] == 0.0
        client.post(f"{base}/{entry_id}/validate", headers=owner_h)
        listed = client.get(base, headers=owner_h).get_json()["entries"]
        assert listed[0]["status"] == "validated"
        assert listed[0]["effective_cost"] == 100.0


class TestListAndStatusFilter:
    def test_manager_logged_entries_are_validated_and_filterable(self, client, linked_h, owner_h, ids):
        base = f"/api/v1/projects/{ids['project']}/labor-entries"
        assert _submit(client, linked_h, ids).status_code == 201
        r = client.post(
            base,
            json={"worker_id": ids["free_worker"], "date": date.today().isoformat(), "shift_type": "half"},
            headers=owner_h,
        )
        assert r.status_code == 201

        both = client.get(base, headers=owner_h).get_json()
        assert both["total"] == 2
        statuses = {e["worker_id"]: e["status"] for e in both["entries"]}
        assert statuses[ids["worker"]] == "pending"
        assert statuses[ids["free_worker"]] == "validated"

        pending = client.get(f"{base}?status=pending", headers=owner_h).get_json()
        assert [e["worker_id"] for e in pending["entries"]] == [ids["worker"]]
        assert pending["entries"][0]["submitted_by_user_id"] == ids["linked"]

        assert client.get(f"{base}?status=bogus", headers=owner_h).status_code == 400


class TestNotificationsBell:
    def test_owner_sees_pending_attendance_member_does_not(self, client, linked_h, owner_h, unlinked_h, ids):
        assert _submit(client, linked_h, ids).status_code == 201

        r = client.get("/api/v1/notifications", headers=owner_h)
        assert r.status_code == 200
        body = r.get_json()
        assert body["items"] == []
        assert body["count"] == 1
        item = body["attendance_pending"][0]
        assert item["kind"] == "attendance_pending"
        assert item["project_id"] == ids["project"]
        assert item["project_name"] == "Chantier AV"
        assert item["worker_name"] == "Linked Worker"
        assert item["shift_type"] == "full"
        assert item["date"] == date.today().isoformat()

        for h in (linked_h, unlinked_h):
            body = client.get("/api/v1/notifications", headers=h).get_json()
            assert body["attendance_pending"] == []
            assert body["count"] == 0

    def test_validation_clears_the_bell(self, client, linked_h, owner_h, ids):
        entry_id = _submit(client, linked_h, ids).get_json()["id"]
        client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry_id}/validate", headers=owner_h)
        body = client.get("/api/v1/notifications", headers=owner_h).get_json()
        assert body["attendance_pending"] == []


class TestWorkerUserLink:
    def test_update_and_list_expose_user_id_and_enforce_uniqueness(self, client, owner_h, ids):
        base = f"/api/v1/projects/{ids['project']}/workers"
        listed = client.get(base, headers=owner_h).get_json()["workers"]
        by_id = {w["id"]: w for w in listed}
        assert by_id[ids["worker"]]["user_id"] == ids["linked"]
        assert by_id[ids["free_worker"]]["user_id"] is None

        # linking the same account to a second worker on the same project is refused
        r = client.put(f"{base}/{ids['free_worker']}", json={"user_id": ids["linked"]}, headers=owner_h)
        assert r.status_code == 400

        # a different account links fine, and null unlinks
        r = client.put(f"{base}/{ids['free_worker']}", json={"user_id": ids["unlinked"]}, headers=owner_h)
        assert r.status_code == 200 and r.get_json()["user_id"] == ids["unlinked"]
        r = client.put(f"{base}/{ids['free_worker']}", json={"user_id": None}, headers=owner_h)
        assert r.status_code == 200 and r.get_json()["user_id"] is None

    def test_unknown_user_id_is_a_clear_400(self, client, owner_h, ids):
        base = f"/api/v1/projects/{ids['project']}/workers"
        r = client.put(f"{base}/{ids['free_worker']}", json={"user_id": str(uuid4())}, headers=owner_h)
        assert r.status_code == 400
        assert "existing user" in r.get_json()["message"]
        r = client.post(base, json={"name": "Ghost", "daily_rate": 50, "user_id": str(uuid4())}, headers=owner_h)
        assert r.status_code == 400

    def test_duplicate_link_on_create_is_400_and_soft_delete_frees_the_slot(self, client, owner_h, ids):
        base = f"/api/v1/projects/{ids['project']}/workers"
        r = client.post(base, json={"name": "Dup", "daily_rate": 50, "user_id": ids["linked"]}, headers=owner_h)
        assert r.status_code == 400
        assert "already linked" in r.get_json()["message"]

        r = client.post(base, json={"name": "Temp", "daily_rate": 60, "user_id": ids["unlinked"]}, headers=owner_h)
        assert r.status_code == 201
        temp_id = r.get_json()["id"]
        assert client.delete(f"{base}/{temp_id}", headers=owner_h).status_code == 204
        # the departed worker's account can be linked to a fresh worker row
        r = client.post(
            base, json={"name": "Back again", "daily_rate": 60, "user_id": ids["unlinked"]}, headers=owner_h
        )
        assert r.status_code == 201
        client.put(f"{base}/{r.get_json()['id']}", json={"user_id": None}, headers=owner_h)
