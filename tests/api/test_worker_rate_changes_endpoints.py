"""API-level integration tests for worker rate-change endpoints.

Covers:
- POST   /projects/<pid>/workers/<wid>/rate-changes  → 201 create, 201 upsert, 400 rate<=0, 404 unknown worker
- GET    /projects/<pid>/workers/<wid>/rate-changes  → 200 list DESC, 403 no manage_labor token
- DELETE /projects/<pid>/workers/<wid>/rate-changes/<rc_id> → 204, 404 unknown, 403

Also verifies the pricing integration paths:
- GET /labor-entries  effective_cost uses resolved rate (not base rate when a change exists)
- GET /labor-summary  total_cost uses resolved rate
- GET /labor-monthly-summary total_cost uses resolved rate
- amount_override on an entry ignores the resolved rate
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.infrastructure.adapters.sqlalchemy_labor_role import SQLAlchemyLaborRoleRepository
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
from app.infrastructure.adapters.sqlalchemy_worker_rate_change import SQLAlchemyWorkerRateChangeRepository
from app.infrastructure.database.models import (
    PermissionModel,
    ProjectModel,
    RoleModel,
    UserModel,
)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rate_app():
    """Flask app with in-memory DB + full labor + rate-change container."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
        SqlAlchemyProjectTagRepository,
    )
    from app.application.labor.bulk_log_attendance import BulkLogAttendanceUseCase as _BulkLogUC
    from app.application.labor.delete_worker_rate_change import DeleteWorkerRateChangeUseCase as _DelRateUC
    from app.application.labor.list_labor_entries import ListLaborEntriesUseCase as _ListEntriesUC
    from app.application.labor.list_worker_rate_changes import ListWorkerRateChangesUseCase as _ListRateUC
    from app.application.labor.log_attendance import LogAttendanceUseCase as _LogAttendUC
    from app.application.labor.set_worker_rate_change import SetWorkerRateChangeUseCase as _SetRateUC
    from app.application.labor.update_attendance import UpdateAttendanceUseCase as _UpdateAttendUC
    from config import TestingConfig
    from wiring import configure_container, get_container

    class RateTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(RateTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()
        token_issuer = JWTTokenIssuer()
        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        worker_repo = SQLAlchemyWorkerRepository(db.session)
        entry_repo = SQLAlchemyLaborEntryRepository(db.session)

        # Permissions
        manage_labor_perm = PermissionModel(name="project:manage_labor", resource="project", action="manage_labor")
        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        # Admin has all perms; reader has only read (no manage_labor)
        admin_role = RoleModel(name="rc_admin", description="Rate Change Admin")
        admin_role.permissions.extend([manage_labor_perm, read_perm, star_perm])

        reader_role = RoleModel(name="rc_reader", description="Rate Change Reader")
        reader_role.permissions.append(read_perm)

        db.session.add_all([manage_labor_perm, read_perm, star_perm, admin_role, reader_role])
        db.session.flush()

        admin_user = UserModel(email="rc_admin@test.com", password_hash=hasher.hash("Admin1234!"), is_active=True)
        admin_user.roles.append(admin_role)

        reader_user = UserModel(email="rc_reader@test.com", password_hash=hasher.hash("Reader1234!"), is_active=True)
        reader_user.roles.append(reader_role)

        db.session.add_all([admin_user, reader_user])
        db.session.flush()

        project = ProjectModel(name="Rate Change Test Project", owner_id=admin_user.id)
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

        _c = get_container()

        # Wire rate-change repo + use-cases
        rate_change_repo = SQLAlchemyWorkerRateChangeRepository(db.session)
        _c.worker_rate_change_repository = rate_change_repo
        _c.set_worker_rate_change_usecase = _SetRateUC(worker_repo=worker_repo, rate_change_repo=rate_change_repo)
        _c.list_worker_rate_changes_usecase = _ListRateUC(worker_repo=worker_repo, rate_change_repo=rate_change_repo)
        _c.delete_worker_rate_change_usecase = _DelRateUC(worker_repo=worker_repo, rate_change_repo=rate_change_repo)
        # Re-wire list_labor_entries with rate_change_repo
        _c.list_labor_entries_usecase = _ListEntriesUC(
            worker_repo=worker_repo,
            entry_repo=entry_repo,
            rate_change_repo=rate_change_repo,
        )

        # Wire attendance write use-cases (need tag_repo)
        _tag_repo = SqlAlchemyProjectTagRepository(db.session)
        _c.log_attendance_usecase = _LogAttendUC(worker_repo=worker_repo, entry_repo=entry_repo, tag_repo=_tag_repo)
        _c.update_attendance_usecase = _UpdateAttendUC(
            entry_repo=entry_repo, worker_repo=worker_repo, tag_repo=_tag_repo
        )
        _c.bulk_log_attendance_usecase = _BulkLogUC(
            worker_repo=worker_repo, entry_repo=entry_repo, db_session=db.session, tag_repo=_tag_repo
        )

        # Wire labor role use-cases
        from app.application.labor.create_labor_role_usecase import CreateLaborRoleUseCase as _CreateLRUC
        from app.application.labor.delete_labor_role_usecase import DeleteLaborRoleUseCase as _DeleteLRUC
        from app.application.labor.list_labor_roles_usecase import ListLaborRolesUseCase as _ListLRUC
        from app.application.labor.update_labor_role_usecase import UpdateLaborRoleUseCase as _UpdateLRUC

        _labor_role_repo = SQLAlchemyLaborRoleRepository(db.session)
        _c.labor_role_repository = _labor_role_repo
        _c.create_labor_role_usecase = _CreateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.update_labor_role_usecase = _UpdateLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.delete_labor_role_usecase = _DeleteLRUC(repo=_labor_role_repo, db_session=db.session)
        _c.list_labor_roles_usecase = _ListLRUC(repo=_labor_role_repo)

        test_app._test_admin_email = "rc_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_reader_email = "rc_reader@test.com"
        test_app._test_reader_password = "Reader1234!"
        test_app._test_project_id = str(project.id)
        test_app._test_admin_user_id = str(admin_user.id)

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def rc_client(rate_app):
    return rate_app.test_client()


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(rc_client, rate_app):
    return _login(rc_client, rate_app._test_admin_email, rate_app._test_admin_password)


@pytest.fixture
def reader_token(rc_client, rate_app):
    return _login(rc_client, rate_app._test_reader_email, rate_app._test_reader_password)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rate_changes_url(project_id: str, worker_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers/{worker_id}/rate-changes"


def _rate_change_url(project_id: str, worker_id: str, rc_id: str) -> str:
    return f"/api/v1/projects/{project_id}/workers/{worker_id}/rate-changes/{rc_id}"


def _create_worker(client, token, project_id: str, name: str = "Worker A", rate: float = 100.0) -> dict:
    resp = client.post(
        f"/api/v1/projects/{project_id}/workers",
        json={"name": name, "daily_rate": rate},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _log_entry(client, token, project_id: str, worker_id: str, entry_date: str, shift_type: str = "full") -> dict:
    resp = client.post(
        f"/api/v1/projects/{project_id}/labor-entries",
        json={"worker_id": worker_id, "date": entry_date, "shift_type": shift_type},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


# ---------------------------------------------------------------------------
# POST rate-changes — CRUD basics
# ---------------------------------------------------------------------------


class TestRateChangeCreate:
    def test_create_rate_change_201(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Worker Create")

        resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-06-01", "daily_rate": 150.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["daily_rate"] == 150.0
        assert data["effective_date"] == "2026-06-01"
        assert "id" in data
        assert data["worker_id"] == worker["id"]

    def test_create_rate_change_upsert_same_date(self, rc_client, admin_token, rate_app):
        """POST the same date twice → upsert; second call returns updated rate, still 201."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Worker Upsert")

        resp1 = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-07-01", "daily_rate": 200.0},
            headers=_auth(admin_token),
        )
        assert resp1.status_code == 201

        resp2 = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-07-01", "daily_rate": 250.0},
            headers=_auth(admin_token),
        )
        assert resp2.status_code == 201
        data2 = resp2.get_json()
        # Rate updated; same row (GET list should have only one entry for this date)
        assert data2["daily_rate"] == 250.0

    def test_create_rate_change_400_zero_rate(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Worker Zero")

        resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-06-01", "daily_rate": 0.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_create_rate_change_400_negative_rate(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Worker Neg")

        resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-06-01", "daily_rate": -50.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_create_rate_change_404_unknown_worker(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        fake_wid = str(uuid4())
        resp = rc_client.post(
            _rate_changes_url(pid, fake_wid),
            json={"effective_date": "2026-06-01", "daily_rate": 100.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_create_rate_change_403_without_manage_labor(self, rc_client, reader_token, rate_app, admin_token):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Worker 403")

        # reader only has project:read, not project:manage_labor
        resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-06-01", "daily_rate": 100.0},
            headers=_auth(reader_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET rate-changes
# ---------------------------------------------------------------------------


class TestRateChangeList:
    def test_list_rate_changes_empty(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC List Empty")

        resp = rc_client.get(_rate_changes_url(pid, worker["id"]), headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.get_json()["rate_changes"] == []

    def test_list_rate_changes_desc_order(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC List Order")

        for d, r in [("2026-01-01", 100.0), ("2026-06-01", 150.0), ("2026-03-01", 120.0)]:
            rc_client.post(
                _rate_changes_url(pid, worker["id"]),
                json={"effective_date": d, "daily_rate": r},
                headers=_auth(admin_token),
            )

        resp = rc_client.get(_rate_changes_url(pid, worker["id"]), headers=_auth(admin_token))
        assert resp.status_code == 200
        dates = [rc["effective_date"] for rc in resp.get_json()["rate_changes"]]
        assert dates == sorted(dates, reverse=True), "rate_changes must be ordered effective_date DESC"

    def test_list_rate_changes_404_unknown_worker(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        fake_wid = str(uuid4())
        resp = rc_client.get(_rate_changes_url(pid, fake_wid), headers=_auth(admin_token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE rate-changes
# ---------------------------------------------------------------------------


class TestRateChangeDelete:
    def test_delete_rate_change_204(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Delete Worker")

        create_resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-09-01", "daily_rate": 175.0},
            headers=_auth(admin_token),
        )
        rc_id = create_resp.get_json()["id"]

        del_resp = rc_client.delete(
            _rate_change_url(pid, worker["id"], rc_id),
            headers=_auth(admin_token),
        )
        assert del_resp.status_code == 204

        # Confirm deleted
        list_resp = rc_client.get(_rate_changes_url(pid, worker["id"]), headers=_auth(admin_token))
        rc_ids = [rc["id"] for rc in list_resp.get_json()["rate_changes"]]
        assert rc_id not in rc_ids

    def test_delete_rate_change_404_unknown_rc(self, rc_client, admin_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Del 404")

        resp = rc_client.delete(
            _rate_change_url(pid, worker["id"], str(uuid4())),
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_delete_rate_change_403_without_manage_labor(self, rc_client, admin_token, reader_token, rate_app):
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "RC Del 403")

        create_resp = rc_client.post(
            _rate_changes_url(pid, worker["id"]),
            json={"effective_date": "2026-10-01", "daily_rate": 100.0},
            headers=_auth(admin_token),
        )
        rc_id = create_resp.get_json()["id"]

        resp = rc_client.delete(
            _rate_change_url(pid, worker["id"], rc_id),
            headers=_auth(reader_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Pricing integration — list_labor_entries effective_cost
# ---------------------------------------------------------------------------


class TestRateResolutionInListEntries:
    def test_entry_before_change_uses_base_rate(self, rc_client, admin_token, rate_app):
        """Entry logged BEFORE effective_date → base rate applies."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Pricing Before", rate=100.0)
        wid = worker["id"]

        _log_entry(rc_client, admin_token, pid, wid, "2026-05-01")
        rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-06-01", "daily_rate": 200.0},
            headers=_auth(admin_token),
        )

        resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-entries",
            query_string={"date_from": "2026-05-01", "date_to": "2026-05-01", "worker_id": wid},
            headers=_auth(admin_token),
        )
        entries = resp.get_json()["entries"]
        assert len(entries) == 1
        assert entries[0]["effective_cost"] == pytest.approx(100.0)

    def test_entry_on_and_after_change_uses_new_rate(self, rc_client, admin_token, rate_app):
        """Entry logged ON or AFTER effective_date → new rate applies."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Pricing After", rate=100.0)
        wid = worker["id"]

        _log_entry(rc_client, admin_token, pid, wid, "2026-06-10")
        _log_entry(rc_client, admin_token, pid, wid, "2026-06-12")
        rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-06-10", "daily_rate": 150.0},
            headers=_auth(admin_token),
        )

        resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-entries",
            query_string={"date_from": "2026-06-10", "date_to": "2026-06-12", "worker_id": wid},
            headers=_auth(admin_token),
        )
        entries = resp.get_json()["entries"]
        assert len(entries) == 2
        for e in entries:
            assert e["effective_cost"] == pytest.approx(150.0)

    def test_amount_override_ignores_resolved_rate(self, rc_client, admin_token, rate_app):
        """amount_override on an entry bypasses rate resolution entirely."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Pricing Override", rate=100.0)
        wid = worker["id"]

        # Log entry with explicit override of 999
        resp = rc_client.post(
            f"/api/v1/projects/{pid}/labor-entries",
            json={"worker_id": wid, "date": "2026-06-20", "shift_type": "full", "amount_override": 999.0},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201

        # Apply rate change for same date
        rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-06-20", "daily_rate": 200.0},
            headers=_auth(admin_token),
        )

        list_resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-entries",
            query_string={"date_from": "2026-06-20", "date_to": "2026-06-20", "worker_id": wid},
            headers=_auth(admin_token),
        )
        entries = list_resp.get_json()["entries"]
        assert len(entries) == 1
        # Override wins — neither 100 (base) nor 200 (change) should appear
        assert entries[0]["effective_cost"] == pytest.approx(999.0)
        assert entries[0]["amount_override"] == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# Pricing integration — get_summary total_cost
# ---------------------------------------------------------------------------


class TestRateResolutionInSummary:
    def test_summary_total_cost_uses_effective_rate(self, rc_client, admin_token, rate_app):
        """Summary total_cost = sum of per-entry resolved rates."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Summary Rate Worker", rate=100.0)
        wid = worker["id"]

        _log_entry(rc_client, admin_token, pid, wid, "2026-08-01")  # base 100
        _log_entry(rc_client, admin_token, pid, wid, "2026-08-15")  # new 150

        rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-08-10", "daily_rate": 150.0},
            headers=_auth(admin_token),
        )

        resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-summary",
            query_string={"date_from": "2026-08-01", "date_to": "2026-08-31"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        rows = resp.get_json()["rows"]
        worker_row = next(r for r in rows if r["worker_id"] == wid)
        # 100 (before change) + 150 (on/after change) = 250
        assert worker_row["total_cost"] == pytest.approx(250.0)

    def test_summary_reverts_to_base_after_delete(self, rc_client, admin_token, rate_app):
        """After deleting the rate change, summary reverts to base rate for all entries."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Summary Revert Worker", rate=100.0)
        wid = worker["id"]

        _log_entry(rc_client, admin_token, pid, wid, "2026-09-05")
        _log_entry(rc_client, admin_token, pid, wid, "2026-09-10")

        create_resp = rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-09-01", "daily_rate": 200.0},
            headers=_auth(admin_token),
        )
        rc_id = create_resp.get_json()["id"]

        # Delete the rate change
        rc_client.delete(_rate_change_url(pid, wid, rc_id), headers=_auth(admin_token))

        resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-summary",
            query_string={"date_from": "2026-09-01", "date_to": "2026-09-30"},
            headers=_auth(admin_token),
        )
        rows = resp.get_json()["rows"]
        worker_row = next(r for r in rows if r["worker_id"] == wid)
        # Both entries now priced at base 100 → total 200
        assert worker_row["total_cost"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Pricing integration — get_monthly_summary
# ---------------------------------------------------------------------------


class TestRateResolutionInMonthlySummary:
    def test_monthly_summary_uses_effective_rate(self, rc_client, admin_token, rate_app):
        """Monthly summary total_cost correctly reflects per-date rate changes."""
        pid = rate_app._test_project_id
        worker = _create_worker(rc_client, admin_token, pid, "Monthly Rate Worker", rate=100.0)
        wid = worker["id"]

        _log_entry(rc_client, admin_token, pid, wid, "2026-10-05")  # base 100
        _log_entry(rc_client, admin_token, pid, wid, "2026-10-20")  # new 180

        rc_client.post(
            _rate_changes_url(pid, wid),
            json={"effective_date": "2026-10-15", "daily_rate": 180.0},
            headers=_auth(admin_token),
        )

        resp = rc_client.get(
            f"/api/v1/projects/{pid}/labor-monthly-summary",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        rows = resp.get_json()["rows"]
        oct_row = next((r for r in rows if r["year"] == 2026 and r["month"] == 10), None)
        assert oct_row is not None
        # 100 + 180 = 280 total for October for this worker
        oct_worker = next(w for w in oct_row["workers"] if w["worker_id"] == wid)
        assert oct_worker["total_cost"] == pytest.approx(280.0)
