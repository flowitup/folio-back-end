"""API-level integration tests for PUT /labor-entries/day-tag.

Bulk sets (or clears) tag_id on every labor entry of a project day. Covers:
- happy path (multi-entry tag + clear)
- 0-entry day (200, updated_count=0)
- cross-project tag rejection (400)
- cross-project IDOR isolation (other project's same-date entries untouched)
- auth/authz: 401 no JWT, 403 non-member
- 400 bad date
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    UserModel,
    ProjectModel,
    ProjectTagModel,
    RoleModel,
    PermissionModel,
)
from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository


# ---------------------------------------------------------------------------
# App fixture — wires labor + tags into the container
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def day_tag_app():
    """Flask app with in-memory DB + labor/tags container for day-tag route tests.

    Two projects are seeded: `project_a` (the target of every day-tag call)
    and `project_b` (used only to prove the bulk UPDATE never leaks across
    projects). The admin user is granted global `*:*` so it can read/write
    both projects without per-project membership rows.
    """
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
        SqlAlchemyProjectTagRepository,
    )
    from app.application.labor.log_attendance import LogAttendanceUseCase
    from app.application.labor.update_attendance import UpdateAttendanceUseCase
    from app.application.labor.bulk_log_attendance import BulkLogAttendanceUseCase
    from app.application.labor.tag_day_usecase import TagDayUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class DayTagTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(DayTagTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        # Seed permissions/roles — names must match require_permission exactly.
        manage_labor_perm = PermissionModel(name="project:manage_labor", resource="project", action="manage_labor")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="day_tag_admin", description="Day Tag Admin")
        admin_role.permissions.extend([manage_labor_perm, read_perm, star_perm])

        outsider_role = RoleModel(name="day_tag_outsider", description="No permissions")

        db.session.add_all([manage_labor_perm, read_perm, star_perm, admin_role, outsider_role])
        db.session.flush()

        admin_user = UserModel(
            email="daytagadmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        outsider_user = UserModel(
            email="daytagoutsider@test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )
        outsider_user.roles.append(outsider_role)

        db.session.add_all([admin_user, outsider_user])
        db.session.flush()

        project_a = ProjectModel(name="Day Tag Project A", owner_id=admin_user.id)
        project_b = ProjectModel(name="Day Tag Project B", owner_id=admin_user.id)
        db.session.add_all([project_a, project_b])
        db.session.commit()

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=token_issuer,
            session_manager=FlaskSessionManager(),
            worker_repository=worker_repo,
            labor_entry_repository=entry_repo,
        )

        _c = get_container()
        _tag_repo = SqlAlchemyProjectTagRepository(db.session)
        _c.log_attendance_usecase = LogAttendanceUseCase(
            worker_repo=worker_repo,
            entry_repo=entry_repo,
            tag_repo=_tag_repo,
        )
        _c.update_attendance_usecase = UpdateAttendanceUseCase(
            entry_repo=entry_repo,
            worker_repo=worker_repo,
            tag_repo=_tag_repo,
        )
        _c.bulk_log_attendance_usecase = BulkLogAttendanceUseCase(
            worker_repo=worker_repo,
            entry_repo=entry_repo,
            db_session=db.session,
            tag_repo=_tag_repo,
        )
        _c.tag_day_usecase = TagDayUseCase(
            entry_repo=entry_repo,
            tag_repo=_tag_repo,
        )

        test_app._test_admin_email = "daytagadmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_outsider_email = "daytagoutsider@test.com"
        test_app._test_outsider_password = "Outsider1234!"
        test_app._test_project_a_id = str(project_a.id)
        test_app._test_project_b_id = str(project_b.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def day_tag_client(day_tag_app):
    return day_tag_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(day_tag_client, day_tag_app):
    return _login(day_tag_client, day_tag_app._test_admin_email, day_tag_app._test_admin_password)


@pytest.fixture
def outsider_token(day_tag_client, day_tag_app):
    return _login(day_tag_client, day_tag_app._test_outsider_email, day_tag_app._test_outsider_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _workers_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers"


def _entries_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-entries"


def _day_tag_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-entries/day-tag"


@pytest.fixture(autouse=True)
def _reset_mutable_rows(day_tag_app):
    """Clear labor entries/workers/tags between tests; keep the base seed.

    Module-scoped app fixture (expensive to rebuild) + function-scoped data
    reset keeps each test's counts/isolation assertions independent.
    """
    from app import db
    from app.infrastructure.database.models import LaborEntryModel, WorkerModel

    with day_tag_app.app_context():
        db.session.query(LaborEntryModel).delete()
        db.session.query(WorkerModel).delete()
        db.session.query(ProjectTagModel).delete()
        db.session.commit()
    yield


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_worker(client, token, project_id: str, name: str) -> str:
    resp = client.post(
        _workers_url(project_id),
        json={"name": name, "daily_rate": 100.0},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _log_full_day(client, token, project_id: str, worker_id: str, date: str, tag_id: str | None = None) -> dict:
    body = {"worker_id": worker_id, "date": date, "shift_type": "full", "supplement_hours": 0}
    if tag_id is not None:
        body["tag_id"] = tag_id
    resp = client.post(_entries_url(project_id), json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _create_tag(app, project_id: str, name: str, color: str = "#FF0000") -> str:
    """Insert a ProjectTagModel row directly — tags CRUD is out of scope here."""
    from app import db
    from uuid import UUID as _UUID

    with app.app_context():
        tag = ProjectTagModel(project_id=_UUID(project_id), name=name, color=color)
        db.session.add(tag)
        db.session.commit()
        return str(tag.id)


def _list_entries(client, token, project_id: str) -> list[dict]:
    resp = client.get(_entries_url(project_id), headers=_auth(token))
    assert resp.status_code == 200
    return resp.get_json()["entries"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDayTagEndpoint:
    def test_happy_path_tags_all_entries_of_day(self, day_tag_client, admin_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        tag_id = _create_tag(day_tag_app, pid, "Fondations")
        w1 = _create_worker(day_tag_client, admin_token, pid, "W1")
        w2 = _create_worker(day_tag_client, admin_token, pid, "W2")
        w3 = _create_worker(day_tag_client, admin_token, pid, "W3")
        for w in (w1, w2, w3):
            _log_full_day(day_tag_client, admin_token, pid, w, "2026-03-01")

        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-01", "tag_id": tag_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["updated_count"] == 3
        assert data["date"] == "2026-03-01"
        assert data["tag_id"] == tag_id

        entries = _list_entries(day_tag_client, admin_token, pid)
        tagged = [e for e in entries if e["date"] == "2026-03-01"]
        assert len(tagged) == 3
        assert all(e["tag_id"] == tag_id for e in tagged)

    def test_null_tag_id_clears_tags(self, day_tag_client, admin_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        tag_id = _create_tag(day_tag_app, pid, "Toiture")
        w1 = _create_worker(day_tag_client, admin_token, pid, "Clear W1")
        w2 = _create_worker(day_tag_client, admin_token, pid, "Clear W2")
        _log_full_day(day_tag_client, admin_token, pid, w1, "2026-03-02", tag_id=tag_id)
        _log_full_day(day_tag_client, admin_token, pid, w2, "2026-03-02", tag_id=tag_id)

        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-02", "tag_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated_count"] == 2
        assert data["tag_id"] is None

        entries = _list_entries(day_tag_client, admin_token, pid)
        cleared = [e for e in entries if e["date"] == "2026-03-02"]
        assert len(cleared) == 2
        assert all(e["tag_id"] is None for e in cleared)

    def test_zero_entry_day_returns_200_zero(self, day_tag_client, admin_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-03", "tag_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated_count"] == 0
        assert data["date"] == "2026-03-03"
        assert data["tag_id"] is None

    def test_tag_from_another_project_rejected_400(self, day_tag_client, admin_token, day_tag_app):
        pid_a = day_tag_app._test_project_a_id
        pid_b = day_tag_app._test_project_b_id
        other_tag_id = _create_tag(day_tag_app, pid_b, "Belongs To B")
        worker = _create_worker(day_tag_client, admin_token, pid_a, "Cross Tag Worker")
        _log_full_day(day_tag_client, admin_token, pid_a, worker, "2026-03-04")

        resp = day_tag_client.put(
            _day_tag_url(pid_a),
            json={"date": "2026-03-04", "tag_id": other_tag_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

        # Entry must remain untouched — the guard runs before any UPDATE.
        entries = _list_entries(day_tag_client, admin_token, pid_a)
        entry = next(e for e in entries if e["date"] == "2026-03-04")
        assert entry["tag_id"] is None

    def test_other_project_entries_same_date_not_touched(self, day_tag_client, admin_token, day_tag_app):
        """IDOR guard: bulk UPDATE must not leak across projects via worker join."""
        pid_a = day_tag_app._test_project_a_id
        pid_b = day_tag_app._test_project_b_id
        tag_id = _create_tag(day_tag_app, pid_a, "A-Only Tag")

        worker_a = _create_worker(day_tag_client, admin_token, pid_a, "Worker A")
        worker_b = _create_worker(day_tag_client, admin_token, pid_b, "Worker B")
        _log_full_day(day_tag_client, admin_token, pid_a, worker_a, "2026-03-05")
        _log_full_day(day_tag_client, admin_token, pid_b, worker_b, "2026-03-05")

        resp = day_tag_client.put(
            _day_tag_url(pid_a),
            json={"date": "2026-03-05", "tag_id": tag_id},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["updated_count"] == 1

        entries_a = _list_entries(day_tag_client, admin_token, pid_a)
        entry_a = next(e for e in entries_a if e["date"] == "2026-03-05")
        assert entry_a["tag_id"] == tag_id

        entries_b = _list_entries(day_tag_client, admin_token, pid_b)
        entry_b = next(e for e in entries_b if e["date"] == "2026-03-05")
        assert entry_b["tag_id"] is None

    def test_non_member_forbidden_403(self, day_tag_client, outsider_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-06", "tag_id": None},
            headers=_auth(outsider_token),
        )
        assert resp.status_code == 403

    def test_no_jwt_unauthorized_401(self, day_tag_client, day_tag_app):
        pid = day_tag_app._test_project_a_id
        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-07", "tag_id": None},
        )
        assert resp.status_code == 401

    def test_bad_date_format_400(self, day_tag_client, admin_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "not-a-date", "tag_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_invalid_calendar_date_400(self, day_tag_client, admin_token, day_tag_app):
        """Passes the YYYY-MM-DD regex but is not a real calendar date."""
        pid = day_tag_app._test_project_a_id
        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-13-40", "tag_id": None},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_nonexistent_tag_id_rejected_400(self, day_tag_client, admin_token, day_tag_app):
        pid = day_tag_app._test_project_a_id
        worker = _create_worker(day_tag_client, admin_token, pid, "Ghost Tag Worker")
        _log_full_day(day_tag_client, admin_token, pid, worker, "2026-03-08")

        resp = day_tag_client.put(
            _day_tag_url(pid),
            json={"date": "2026-03-08", "tag_id": str(uuid4())},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
