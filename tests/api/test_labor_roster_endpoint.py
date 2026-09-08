"""API integration tests: GET /projects/<id>/labor/roster (D3 day roster).

Layout: company_a (admin_a, manager_assigned + member_assigned both assigned
to project_a, member_unassigned NOT assigned) and company_b (admin_b, no
relation to project_a) plus a true outsider with no company/project relation
anywhere. Mirrors the two-company fixture style of
tests/api/test_authz_resolver_integration.py and tests/api/test_projects_tenancy.py.

Covers the plan's D3 acceptance criteria:
  - admin / manager / member assigned to the project → 200, exact 5-field
    whitelist per row, never rate/cost/amount/daily_rate/total.
  - member of the SAME company but NOT assigned to the project → 404.
  - admin of a DIFFERENT company (no relation to project_a) → 404.
  - authenticated user with no company relationship anywhere → 404.
  - status/hours/day_type derived correctly from shift_type + supplement_hours,
    a pending self-logged entry shows "pending", a worker with no entry that
    day shows "absent".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel, WorkerModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD = "Pass1234!"
ROSTER_DATE = "2026-04-06"


@pytest.fixture(scope="module")
def roster_app():
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig

    class RosterTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(RosterTestConfig)

    with test_app.app_context():
        db.create_all()
        hasher = Argon2PasswordHasher()
        now = datetime.now(timezone.utc)

        def user(email: str) -> UserModel:
            u = UserModel(email=email, password_hash=hasher.hash(PASSWORD), is_active=True)
            db.session.add(u)
            return u

        admin_a = user("roster_admin_a@test.com")
        manager_assigned = user("roster_manager_assigned@test.com")
        member_assigned = user("roster_member_assigned@test.com")
        member_unassigned = user("roster_member_unassigned@test.com")
        admin_b = user("roster_admin_b@test.com")
        user("roster_outsider@test.com")  # login only, never assigned an id
        db.session.flush()

        company_a = CompanyModel(
            id=uuid4(),
            legal_name="Roster Co A",
            address="1 rue A",
            created_by=admin_a.id,
            created_at=now,
            updated_at=now,
        )
        company_b = CompanyModel(
            id=uuid4(),
            legal_name="Roster Co B",
            address="2 rue B",
            created_by=admin_b.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_a, company_b])
        db.session.flush()

        project_a = ProjectModel(name="Roster Project A", owner_id=admin_a.id, company_id=company_a.id)
        db.session.add(project_a)
        db.session.flush()

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=admin_a.id, company_id=company_a.id, role="admin", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=manager_assigned.id,
                    company_id=company_a.id,
                    role="manager",
                    is_primary=True,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=member_assigned.id,
                    company_id=company_a.id,
                    role="member",
                    is_primary=True,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=member_unassigned.id,
                    company_id=company_a.id,
                    role="member",
                    is_primary=True,
                    attached_at=now,
                ),
                UserCompanyAccessModel(
                    user_id=admin_b.id, company_id=company_b.id, role="admin", is_primary=True, attached_at=now
                ),
            ]
        )
        db.session.flush()

        from sqlalchemy import text as _text

        for uid in (manager_assigned.id, member_assigned.id):
            db.session.execute(
                _text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
                {"uid": str(uid), "pid": str(project_a.id), "at": now},
            )

        # Three workers: one full day, one supplement-only pending self-log,
        # one with no entry at all that day (→ "absent").
        worker_full = WorkerModel(project_id=project_a.id, name="Full Day Worker", daily_rate=100)
        worker_pending = WorkerModel(project_id=project_a.id, name="Pending Worker", daily_rate=90)
        worker_absent = WorkerModel(project_id=project_a.id, name="Absent Worker", daily_rate=80)
        db.session.add_all([worker_full, worker_pending, worker_absent])
        db.session.flush()

        db.session.add(
            LaborEntryModel(
                worker_id=worker_full.id,
                date=date.fromisoformat(ROSTER_DATE),
                shift_type="half",
                supplement_hours=1,
                status="validated",
            )
        )
        db.session.add(
            LaborEntryModel(
                worker_id=worker_pending.id,
                date=date.fromisoformat(ROSTER_DATE),
                shift_type=None,
                supplement_hours=3,
                status="pending",
            )
        )
        db.session.commit()

        from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
        from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
        from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
        from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
        from app.infrastructure.adapters.flask_session import FlaskSessionManager
        from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
        from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
            SqlAlchemyCompanyRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
            SqlAlchemyUserCompanyAccessRepository,
        )
        from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
        from wiring import configure_container, get_container

        configure_container(
            user_repository=SQLAlchemyUserRepository(db.session),
            project_repository=SQLAlchemyProjectRepository(db.session),
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            worker_repository=SQLAlchemyWorkerRepository(db.session),
            labor_entry_repository=SQLAlchemyLaborEntryRepository(db.session),
        )
        _c = get_container()
        _c.company_repo = SqlAlchemyCompanyRepository(db.session)
        _c.user_company_access_repo = SqlAlchemyUserCompanyAccessRepository(db.session)
        _c.authz_reader = SqlAlchemyAuthzReader(db.session)

        from app.application.labor.get_day_roster_usecase import GetDayRosterUseCase

        _c.get_day_roster_usecase = GetDayRosterUseCase(
            worker_repo=_c.worker_repository,
            entry_repo=_c.labor_entry_repository,
            authz_reader=_c.authz_reader,
        )

        test_app._project_a_id = project_a.id
        test_app._worker_full_id = worker_full.id
        test_app._worker_pending_id = worker_pending.id
        test_app._worker_absent_id = worker_absent.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(roster_app):
    return roster_app.test_client()


def _login(client, email: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return {"Authorization": f"Bearer {resp.get_json()['access_token']}"}


@pytest.fixture
def admin_a_h(client):
    return _login(client, "roster_admin_a@test.com")


@pytest.fixture
def manager_assigned_h(client):
    return _login(client, "roster_manager_assigned@test.com")


@pytest.fixture
def member_assigned_h(client):
    return _login(client, "roster_member_assigned@test.com")


@pytest.fixture
def member_unassigned_h(client):
    return _login(client, "roster_member_unassigned@test.com")


@pytest.fixture
def admin_b_h(client):
    return _login(client, "roster_admin_b@test.com")


@pytest.fixture
def outsider_h(client):
    return _login(client, "roster_outsider@test.com")


def _roster_url(project_id) -> str:
    return f"/api/v1/projects/{project_id}/labor/roster?date={ROSTER_DATE}"


# ---------------------------------------------------------------------------
# Positive access: admin / manager / member all assigned/implicit → 200.
# ---------------------------------------------------------------------------


def _rows_by_worker(body: dict) -> dict:
    return {row["worker_id"]: row for row in body["rows"]}


def test_admin_sees_full_roster(client, admin_a_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_a_h)
    assert resp.status_code == 200
    rows = _rows_by_worker(resp.get_json())
    assert set(rows.keys()) == {
        str(roster_app._worker_full_id),
        str(roster_app._worker_pending_id),
        str(roster_app._worker_absent_id),
    }


def test_manager_assigned_sees_full_roster(client, manager_assigned_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=manager_assigned_h)
    assert resp.status_code == 200
    assert len(resp.get_json()["rows"]) == 3


def test_member_assigned_sees_full_roster(client, member_assigned_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=member_assigned_h)
    assert resp.status_code == 200
    assert len(resp.get_json()["rows"]) == 3


# ---------------------------------------------------------------------------
# Row shape: exact 5-field whitelist, no money, correct status/hours/day_type.
# ---------------------------------------------------------------------------


def test_row_shape_has_no_money_fields(client, admin_a_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_a_h)
    body = resp.get_json()
    for row in body["rows"]:
        assert set(row.keys()) == {"worker_id", "name", "status", "hours", "day_type"}
        for forbidden in ("rate", "cost", "amount", "daily_rate", "total"):
            assert forbidden not in row


def test_full_day_row_status_hours_day_type(client, admin_a_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_a_h)
    row = _rows_by_worker(resp.get_json())[str(roster_app._worker_full_id)]
    assert row["status"] == "present"
    assert row["day_type"] == "half"
    # half-day base (4h) + 1 supplement hour.
    assert row["hours"] == 5.0


def test_pending_supplement_only_row(client, admin_a_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_a_h)
    row = _rows_by_worker(resp.get_json())[str(roster_app._worker_pending_id)]
    assert row["status"] == "pending"
    assert row["day_type"] is None
    assert row["hours"] == 3.0


def test_worker_without_entry_is_absent(client, admin_a_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_a_h)
    row = _rows_by_worker(resp.get_json())[str(roster_app._worker_absent_id)]
    assert row["status"] == "absent"
    assert row["hours"] == 0.0
    assert row["day_type"] is None


# ---------------------------------------------------------------------------
# Negative access: hidden as 404, never 403 — the endpoint must not confirm
# the project's existence to a caller unrelated to it.
# ---------------------------------------------------------------------------


def test_member_unassigned_gets_404(client, member_unassigned_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=member_unassigned_h)
    assert resp.status_code == 404


def test_admin_of_different_company_gets_404(client, admin_b_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=admin_b_h)
    assert resp.status_code == 404


def test_authenticated_stranger_gets_404(client, outsider_h, roster_app):
    resp = client.get(_roster_url(roster_app._project_a_id), headers=outsider_h)
    assert resp.status_code == 404


def test_missing_project_gets_404(client, admin_a_h):
    resp = client.get(_roster_url(uuid4()), headers=admin_a_h)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Query validation.
# ---------------------------------------------------------------------------


def test_missing_date_query_param_gets_400(client, admin_a_h, roster_app):
    resp = client.get(f"/api/v1/projects/{roster_app._project_a_id}/labor/roster", headers=admin_a_h)
    assert resp.status_code == 400


def test_invalid_date_query_param_gets_400(client, admin_a_h, roster_app):
    resp = client.get(f"/api/v1/projects/{roster_app._project_a_id}/labor/roster?date=2026-02-30", headers=admin_a_h)
    assert resp.status_code == 400
