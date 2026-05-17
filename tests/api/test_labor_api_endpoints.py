"""API-level integration tests for labor endpoints.

Covers:
- POST/GET/PUT/DELETE labor-entries routes (entry_routes.py)
- GET/POST/PUT/DELETE workers routes (worker_routes.py)
- Pydantic schema 422 validation paths (supplement_hours bounds, empty row, override-without-shift)
- list_labor_entries use-case execution path
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.database.models import (
    UserModel,
    ProjectModel,
    RoleModel,
    PermissionModel,
)
from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository


# ---------------------------------------------------------------------------
# App fixture — wires labor repos into container
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def labor_app():
    """Flask app with in-memory DB + full labor container for route tests."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from config import TestingConfig
    from wiring import configure_container

    class LaborTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(LaborTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        # Seed permissions and roles — names must match exactly what require_permission checks.
        manage_labor_perm = PermissionModel(name="project:manage_labor", resource="project", action="manage_labor")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="labor_admin", description="Labor Admin")
        admin_role.permissions.append(manage_labor_perm)
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(star_perm)

        db.session.add_all([manage_labor_perm, read_perm, star_perm, admin_role])
        db.session.flush()

        # Seed user
        admin_user = UserModel(
            email="laboradmin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.flush()

        # Seed project
        project = ProjectModel(
            name="Labor API Test Project",
            owner_id=admin_user.id,
        )
        db.session.add(project)
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

        # Wire labor role use-cases (added in feat/labor-roles phase 03).
        # The configure_container() signature predates labor roles — wire
        # them directly on the container after the initial wiring completes.
        from wiring import get_container as _get_container
        from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase as _CreateLRUC
        from app.application.labor.update_labor_role_usecase import UpdateLaborRoleUseCase as _UpdateLRUC
        from app.application.labor.delete_labor_role_usecase import DeleteLaborRoleUseCase as _DeleteLRUC
        from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase as _ListLRUC

        _c = _get_container()
        _labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
        _c.labor_role_repository = _labor_role_repo
        _c.create_labor_role_usecase = _CreateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.update_labor_role_usecase = _UpdateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.delete_labor_role_usecase = _DeleteLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.list_labor_roles_usecase = _ListLRUC(repo=_labor_role_repo)

        test_app._test_admin_email = "laboradmin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def labor_client(labor_app):
    return labor_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(labor_client, labor_app):
    return _login(labor_client, labor_app._test_admin_email, labor_app._test_admin_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _workers_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers"


def _worker_url(project_id: str, worker_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers/{worker_id}"


def _entries_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-entries"


def _entry_url(project_id: str, entry_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-entries/{entry_id}"


def _summary_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-summary"


def _monthly_summary_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/labor-monthly-summary"


# ---------------------------------------------------------------------------
# Worker routes
# ---------------------------------------------------------------------------


class TestWorkerRoutes:
    def test_list_workers_empty(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.get(_workers_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "workers" in data
        assert data["total"] == 0

    def test_create_worker_success(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.post(
            _workers_url(pid),
            json={"name": "Test Worker", "daily_rate": 100.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Test Worker"
        assert "id" in data

    def test_create_worker_validation_error(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        # daily_rate must be > 0
        resp = labor_client.post(
            _workers_url(pid),
            json={"name": "X", "daily_rate": -5.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_update_worker_success(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        # Create first
        create_resp = labor_client.post(
            _workers_url(pid),
            json={"name": "Worker To Update", "daily_rate": 80.0},
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        worker_id = create_resp.get_json()["id"]

        # Update
        resp = labor_client.put(
            _worker_url(pid, worker_id),
            json={"name": "Updated Worker"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated Worker"

    def test_update_worker_not_found(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.put(
            _worker_url(pid, str(uuid4())),
            json={"name": "Ghost"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_delete_worker_success(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        create_resp = labor_client.post(
            _workers_url(pid),
            json={"name": "Worker To Delete", "daily_rate": 90.0},
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        worker_id = create_resp.get_json()["id"]

        resp = labor_client.delete(_worker_url(pid, worker_id), headers=_auth(admin_token))
        assert resp.status_code == 204

    def test_delete_worker_not_found(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.delete(_worker_url(pid, str(uuid4())), headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_list_workers_returns_created_workers(self, labor_client, admin_token, labor_app):
        """GET /workers returns workers after creation — covers list path body."""
        pid = labor_app._test_project_id
        # Create one worker
        labor_client.post(
            _workers_url(pid),
            json={"name": "Listed Worker", "daily_rate": 120.0},
            headers=_auth(admin_token),
        )
        resp = labor_client.get(_workers_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        names = [w["name"] for w in data["workers"]]
        assert "Listed Worker" in names

    def test_create_worker_missing_name_raises_400(self, labor_client, admin_token, labor_app):
        """Missing required name field → 400 (Pydantic validation)."""
        pid = labor_app._test_project_id
        resp = labor_client.post(
            _workers_url(pid),
            json={"daily_rate": 100.0},  # name missing
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_update_worker_invalid_rate_raises_400(self, labor_client, admin_token, labor_app):
        """daily_rate=0 → 400 (Pydantic gt=0 constraint)."""
        pid = labor_app._test_project_id
        create_resp = labor_client.post(
            _workers_url(pid),
            json={"name": "Rate Validation Worker", "daily_rate": 80.0},
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        worker_id = create_resp.get_json()["id"]
        resp = labor_client.put(
            _worker_url(pid, worker_id),
            json={"daily_rate": 0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Labor entry routes
# ---------------------------------------------------------------------------


class TestLaborEntryRoutes:
    def _create_worker(self, labor_client, admin_token, labor_app, name="API Worker") -> str:
        pid = labor_app._test_project_id
        resp = labor_client.post(
            _workers_url(pid),
            json={"name": name, "daily_rate": 100.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_list_entries_empty(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.get(_entries_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entries" in data

    def test_log_attendance_full_shift(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Full Shift Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-01",
                "shift_type": "full",
                "supplement_hours": 0,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["shift_type"] == "full"
        assert data["supplement_hours"] == 0

    def test_log_attendance_supplement_only(self, labor_client, admin_token, labor_app):
        """shift_type=None + supplement_hours=3 is valid."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Supplement Only Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-02",
                "shift_type": None,
                "supplement_hours": 3,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["shift_type"] is None
        assert data["supplement_hours"] == 3

    def test_log_attendance_supplement_negative_422(self, labor_client, admin_token, labor_app):
        """supplement_hours=-1 → 422 (Pydantic ge=0 constraint)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Range Test Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-03",
                "shift_type": "full",
                "supplement_hours": -1,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_log_attendance_supplement_above_cap_422(self, labor_client, admin_token, labor_app):
        """supplement_hours=13 → 422 (Pydantic le=12 constraint)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Cap Test Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-04",
                "shift_type": "full",
                "supplement_hours": 13,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_log_attendance_empty_row_422(self, labor_client, admin_token, labor_app):
        """shift_type=None AND supplement_hours=0 → 400 (model_validator empty row)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Empty Row Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-05",
                "shift_type": None,
                "supplement_hours": 0,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_log_attendance_override_without_shift_422(self, labor_client, admin_token, labor_app):
        """shift_type=None + amount_override set → 400 (model_validator)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Override No Shift Worker")
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-02-06",
                "shift_type": None,
                "supplement_hours": 5,
                "amount_override": 50.0,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_update_attendance_success(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Update Test Worker")
        # Create entry
        create_resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-03-01",
                "shift_type": "full",
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        entry_id = create_resp.get_json()["id"]

        # Update supplement_hours only
        resp = labor_client.put(
            _entry_url(pid, entry_id),
            json={"supplement_hours": 4},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["supplement_hours"] == 4

    def test_update_attendance_not_found(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.put(
            _entry_url(pid, str(uuid4())),
            json={"note": "ghost"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_delete_attendance_success(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Delete Entry Worker")
        create_resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-04-01",
                "shift_type": "half",
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        entry_id = create_resp.get_json()["id"]

        resp = labor_client.delete(_entry_url(pid, entry_id), headers=_auth(admin_token))
        assert resp.status_code == 204

        # Regression: a 204 alone is not proof of deletion — verify the row
        # is actually gone via the list endpoint. The earlier version of
        # delete() returned 204 in production but never committed.
        list_resp = labor_client.get(
            _entries_url(pid) + "?from=2026-04-01&to=2026-04-30",
            headers=_auth(admin_token),
        )
        assert list_resp.status_code == 200
        ids = [e["id"] for e in list_resp.get_json()["entries"]]
        assert entry_id not in ids

    def test_delete_attendance_not_found(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.delete(_entry_url(pid, str(uuid4())), headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_get_labor_summary(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.get(_summary_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rows" in data
        assert "total_banked_hours" in data
        assert "total_bonus_days" in data
        assert "total_bonus_cost" in data

    def test_get_labor_monthly_summary_buckets_across_months(self, labor_client, admin_token, labor_app):
        """Per-month rollup endpoint returns one row per (year, month) DESC."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Monthly API Worker")

        for d in ("2026-01-15", "2026-02-10", "2026-02-11"):
            create = labor_client.post(
                _entries_url(pid),
                json={"worker_id": worker_id, "date": d, "shift_type": "full"},
                headers=_auth(admin_token),
            )
            assert create.status_code == 201, create.get_json()

        resp = labor_client.get(_monthly_summary_url(pid), headers=_auth(admin_token))
        assert resp.status_code == 200

        rows = resp.get_json()["rows"]
        # Filter to the (year, month) buckets touched by this worker so the
        # test is not coupled to data seeded by other tests in the module.
        touched = {(r["year"], r["month"]): r for r in rows}
        assert (2026, 2) in touched
        assert (2026, 1) in touched

        feb = touched[(2026, 2)]
        jan = touched[(2026, 1)]
        # New worker contributes 2 priced days in Feb, 1 in Jan. Other tests
        # in the module may have added entries for the same months under
        # different workers, so assert "at least" rather than exact equality.
        assert feb["total_days"] >= 2
        assert jan["total_days"] >= 1

        # DESC ordering: Feb before Jan within the response list.
        ordered_keys = [(r["year"], r["month"]) for r in rows]
        assert ordered_keys.index((2026, 2)) < ordered_keys.index((2026, 1))

        # Each month row carries a `workers` array with the per-worker
        # breakdown — at minimum the worker we just created appears.
        feb_row = touched[(2026, 2)]
        assert "workers" in feb_row
        feb_names = [w["worker_name"] for w in feb_row["workers"]]
        assert "Monthly API Worker" in feb_names

    def test_get_labor_monthly_summary_empty_project_returns_empty_rows(self, labor_client, admin_token, labor_app):
        """Endpoint returns 200 with rows=[] for a project with no entries."""
        # Create an isolated project on the fly via the app's session so
        # the endpoint hits the real route + use case path. PG_UUID columns
        # need UUID(...) objects on SQLite, not strings.
        from uuid import UUID as _UUID

        from app import db
        from app.infrastructure.database.models import ProjectModel

        with labor_app.app_context():
            empty_project = ProjectModel(
                name="Monthly Summary Empty Project",
                owner_id=_UUID(labor_app._test_admin_user_id),
            )
            db.session.add(empty_project)
            db.session.commit()
            empty_pid = str(empty_project.id)

        resp = labor_client.get(_monthly_summary_url(empty_pid), headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.get_json() == {"rows": []}

    def test_list_entries_with_date_filter(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.get(
            _entries_url(pid) + "?from=2026-01-01&to=2026-12-31",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200

    def test_list_entries_invalid_date(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.get(
            _entries_url(pid) + "?from=not-a-date",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_list_entries_default_unbounded_returns_multi_month_history(self, labor_client, admin_token, labor_app):
        """No from/to → returns rows across multiple months (the new attendance default)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "All History Worker")

        # Create one entry in two distinct months.
        for d in ("2026-02-15", "2026-03-15"):
            create = labor_client.post(
                _entries_url(pid),
                json={"worker_id": worker_id, "date": d, "shift_type": "full"},
                headers=_auth(admin_token),
            )
            assert create.status_code == 201, create.get_json()

        resp = labor_client.get(_entries_url(pid), headers=_auth(admin_token))

        assert resp.status_code == 200
        dates = {e["date"] for e in resp.get_json()["entries"] if e["worker_id"] == worker_id}
        assert "2026-02-15" in dates
        assert "2026-03-15" in dates

    def test_list_entries_explicit_limit_caps_response(self, labor_client, admin_token, labor_app):
        """?limit=N returns at most N rows (most recent)."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Limit Worker")

        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            create = labor_client.post(
                _entries_url(pid),
                json={"worker_id": worker_id, "date": d, "shift_type": "full"},
                headers=_auth(admin_token),
            )
            assert create.status_code == 201, create.get_json()

        resp = labor_client.get(
            _entries_url(pid) + f"?worker_id={worker_id}&limit=2",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        entries = resp.get_json()["entries"]
        assert len(entries) == 2
        # Order: date desc → 06-03, 06-02
        assert entries[0]["date"] == "2026-06-03"
        assert entries[1]["date"] == "2026-06-02"

    def test_list_entries_invalid_limit(self, labor_client, admin_token, labor_app):
        """?limit=0 / non-int → 400."""
        pid = labor_app._test_project_id
        resp = labor_client.get(_entries_url(pid) + "?limit=0", headers=_auth(admin_token))
        assert resp.status_code == 400
        resp = labor_client.get(_entries_url(pid) + "?limit=abc", headers=_auth(admin_token))
        assert resp.status_code == 400

    def test_log_attendance_worker_not_found(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": str(uuid4()),
                "date": "2026-05-01",
                "shift_type": "full",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_log_attendance_duplicate_raises_conflict(self, labor_client, admin_token, labor_app):
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Dup Worker")
        payload = {
            "worker_id": worker_id,
            "date": "2026-06-15",
            "shift_type": "full",
        }
        labor_client.post(_entries_url(pid), json=payload, headers=_auth(admin_token))
        resp = labor_client.post(_entries_url(pid), json=payload, headers=_auth(admin_token))
        assert resp.status_code == 409

    def test_standalone_supplement_row_round_trips_through_to_entity(self, labor_client, admin_token, labor_app):
        """Regression: shift_type=None entry round-trips through _to_entity correctly."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Round Trip Worker")

        # Create supplement-only row
        create_resp = labor_client.post(
            _entries_url(pid),
            json={
                "worker_id": worker_id,
                "date": "2026-07-01",
                "shift_type": None,
                "supplement_hours": 5,
            },
            headers=_auth(admin_token),
        )
        assert create_resp.status_code == 201
        created = create_resp.get_json()
        entry_id = created["id"]

        # Verify it appears in the list
        list_resp = labor_client.get(
            _entries_url(pid) + "?from=2026-07-01&to=2026-07-31",
            headers=_auth(admin_token),
        )
        assert list_resp.status_code == 200
        entries = list_resp.get_json()["entries"]
        matching = [e for e in entries if e["id"] == entry_id]
        assert len(matching) == 1
        assert matching[0]["shift_type"] is None
        assert matching[0]["supplement_hours"] == 5
        assert matching[0]["effective_cost"] == 0.0

    def test_summary_banked_hours_aggregation(self, labor_client, admin_token, labor_app):
        """Summary endpoint aggregates supplement_hours into banked_hours correctly."""
        pid = labor_app._test_project_id
        worker_id = self._create_worker(labor_client, admin_token, labor_app, "Banked Worker")

        # Post 3 supplement-only entries: 3+3+2 = 8 total banked
        for day, hours in [(10, 3), (11, 3), (12, 2)]:
            resp = labor_client.post(
                _entries_url(pid),
                json={
                    "worker_id": worker_id,
                    "date": f"2026-08-{day:02d}",
                    "shift_type": None,
                    "supplement_hours": hours,
                },
                headers=_auth(admin_token),
            )
            assert resp.status_code == 201

        summary_resp = labor_client.get(
            _summary_url(pid) + "?from=2026-08-01&to=2026-08-31",
            headers=_auth(admin_token),
        )
        assert summary_resp.status_code == 200
        data = summary_resp.get_json()

        # Find this worker's row
        matching = [r for r in data["rows"] if r["worker_name"] == "Banked Worker"]
        assert len(matching) == 1
        row = matching[0]
        assert row["banked_hours"] == 8  # 3+3+2
        assert row["bonus_full_days"] == 1  # 8//8 = 1
        assert row["bonus_half_days"] == 0  # (8%8)=0 < 4
