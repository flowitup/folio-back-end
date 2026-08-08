"""API integration tests for the Labor Payments Hub summary endpoint + list filters.

Covers:
- GET /projects/<id>/labor-payments-summary — multi-month multi-worker
  aggregation, unassigned bucket (never-linked worker_id), no-service_month
  bucket ordering (always last, year/month null), non-labor exclusion,
  empty project, 403 non-member.
- GET /projects/<id>/invoices — ?service_month=/?worker_id= filters, composed
  with ?type=, and 422 on malformed service_month/worker_id.

Deleted-worker → unassigned is proven at the repo level
(tests/unit/infrastructure/test_labor_payments_summary_repo.py) and the FK
ON DELETE SET NULL mechanics at
tests/unit/infrastructure/test_invoice_worker_id_fk_set_null.py — both are
equivalent to "worker_id is NULL", which is what this file exercises via
never-linked invoices.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.worker import WorkerModel


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pay_app():
    """Flask app wired with invoice use-cases (incl. labor-payments-summary)."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_worker_reader import SQLAlchemyWorkerReader
    from app.application.invoice.create_invoice import CreateInvoiceUseCase
    from app.application.invoice.get_labor_payments_summary_usecase import GetLaborPaymentsSummaryUseCase
    from app.application.invoice import ListInvoicesUseCase, GetInvoiceUseCase, DeleteInvoiceUseCase
    from config import TestingConfig
    from wiring import configure_container, get_container

    class PayTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(PayTestConfig)

    with test_app.app_context():
        db.create_all()

        hasher = Argon2PasswordHasher()

        read_perm = PermissionModel(name="project:read", resource="project", action="read")
        manage_perm = PermissionModel(name="project:manage_invoices", resource="project", action="manage_invoices")
        star_perm = PermissionModel(name="*:*", resource="*", action="*")

        admin_role = RoleModel(name="pay_admin", description="Admin")
        admin_role.permissions.append(read_perm)
        admin_role.permissions.append(manage_perm)
        admin_role.permissions.append(star_perm)

        db.session.add_all([read_perm, manage_perm, star_perm, admin_role])
        db.session.flush()

        admin_user = UserModel(
            email="pay_admin@test.com",
            password_hash=hasher.hash("Admin1234!"),
            is_active=True,
        )
        admin_user.roles.append(admin_role)

        # No roles/membership at all — used for the 403 non-member test.
        outsider_user = UserModel(
            email="pay_outsider@test.com",
            password_hash=hasher.hash("Outsider1234!"),
            is_active=True,
        )

        db.session.add_all([admin_user, outsider_user])
        db.session.commit()

        user_repo = SQLAlchemyUserRepository(db.session)
        project_repo = SQLAlchemyProjectRepository(db.session)
        invoice_repo = SQLAlchemyInvoiceRepository(db.session)
        worker_reader = SQLAlchemyWorkerReader(db.session)

        configure_container(
            user_repository=user_repo,
            project_repository=project_repo,
            password_hasher=hasher,
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invoice_repository=invoice_repo,
        )

        _c = get_container()
        _c.worker_reader = worker_reader
        _c.create_invoice_usecase = CreateInvoiceUseCase(invoice_repo=invoice_repo, worker_reader=worker_reader)
        _c.list_invoices_usecase = ListInvoicesUseCase(invoice_repo)
        _c.get_invoice_usecase = GetInvoiceUseCase(invoice_repo)
        _c.delete_invoice_usecase = DeleteInvoiceUseCase(invoice_repo, None, None)
        _c.get_labor_payments_summary_usecase = GetLaborPaymentsSummaryUseCase(invoice_repo)

        test_app._test_admin_email = "pay_admin@test.com"
        test_app._test_admin_password = "Admin1234!"
        test_app._test_outsider_email = "pay_outsider@test.com"
        test_app._test_outsider_password = "Outsider1234!"
        test_app._test_admin_user_id = admin_user.id

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def pay_client(pay_app):
    return pay_app.test_client()


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture
def admin_token(pay_client, pay_app):
    return _login(pay_client, pay_app._test_admin_email, pay_app._test_admin_password)


@pytest.fixture
def outsider_token(pay_client, pay_app):
    return _login(pay_client, pay_app._test_outsider_email, pay_app._test_outsider_password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def project(pay_app):
    """A fresh, isolated project per test — the summary/list endpoints aggregate
    over ALL of a project's invoices, so tests must not share one project."""
    from app import db

    p = ProjectModel(id=uuid4(), name=f"Pay Project {uuid4().hex[:8]}", owner_id=pay_app._test_admin_user_id)
    db.session.add(p)
    db.session.commit()
    return p


def _summary_url(project_id):
    return f"/api/v1/projects/{project_id}/labor-payments-summary"


def _invoices_url(project_id):
    return f"/api/v1/projects/{project_id}/invoices"


def _create_worker(project_id, name, person_name=None):
    from app import db

    person_id = None
    if person_name:
        # created_by_user_id is nullable=False in the model; resolve to admin.
        admin_id = db.session.query(UserModel.id).filter_by(email="pay_admin@test.com").scalar()
        person = PersonModel(
            id=uuid4(),
            name=person_name,
            normalized_name=person_name.lower(),
            created_by_user_id=admin_id,
        )
        db.session.add(person)
        db.session.flush()
        person_id = person.id

    worker = WorkerModel(
        id=uuid4(),
        project_id=project_id,
        person_id=person_id,
        name=name,
        daily_rate=Decimal("100.00"),
        is_active=True,
    )
    db.session.add(worker)
    db.session.commit()
    return worker


def _post_labor_invoice(client, token, project_id, *, worker_id=None, service_month=None, amount, notes=""):
    body = {
        "type": "labor",
        "issue_date": date.today().isoformat(),
        "recipient_name": "Worker Payroll",
        "notes": notes,
        "items": [{"description": "Labor", "quantity": 1, "unit_price": amount}],
    }
    if worker_id is not None:
        body["worker_id"] = worker_id
    if service_month is not None:
        body["service_month"] = service_month
    resp = client.post(_invoices_url(project_id), json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _post_non_labor_invoice(client, token, project_id, amount):
    body = {
        "type": "materials_services",
        "issue_date": date.today().isoformat(),
        "recipient_name": "Supplier Co",
        "items": [{"description": "Materials", "quantity": 1, "unit_price": amount}],
    }
    resp = client.post(_invoices_url(project_id), json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------


class TestLaborPaymentsSummaryEndpoint:
    def test_empty_project_returns_empty_months(self, pay_client, admin_token, project):
        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.get_json() == {"months": []}

    def test_multi_month_multi_worker_aggregation(self, pay_client, admin_token, project):
        alice = _create_worker(project.id, "Alice")
        bob = _create_worker(project.id, "Bob")

        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(alice.id), service_month="2026-07-15", amount=500
        )
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(alice.id), service_month="2026-07-01", amount=300
        )
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(bob.id), service_month="2026-07-01", amount=400
        )
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(bob.id), service_month="2026-06-01", amount=250
        )

        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()

        assert [(m["year"], m["month"]) for m in data["months"]] == [(2026, 7), (2026, 6)]

        july = data["months"][0]
        assert july["total_paid"] == 1200.0
        assert july["unassigned_paid"] == 0.0
        assert july["unassigned_count"] == 0
        by_worker = {w["worker_id"]: w for w in july["workers"]}
        assert by_worker[str(alice.id)]["paid"] == 800.0
        assert by_worker[str(alice.id)]["invoice_count"] == 2
        assert by_worker[str(alice.id)]["worker_name"] == "Alice"
        assert by_worker[str(bob.id)]["paid"] == 400.0
        assert by_worker[str(bob.id)]["invoice_count"] == 1

        june = data["months"][1]
        assert june["total_paid"] == 250.0
        assert len(june["workers"]) == 1
        assert june["workers"][0]["worker_id"] == str(bob.id)

    def test_worker_name_prefers_linked_person(self, pay_client, admin_token, project):
        worker = _create_worker(project.id, "Legacy Name", person_name="Jean Dupont")
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(worker.id), service_month="2026-08-01", amount=100
        )

        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        data = resp.get_json()
        assert data["months"][0]["workers"][0]["worker_name"] == "Jean Dupont"

    def test_unassigned_bucket_for_invoices_without_worker(self, pay_client, admin_token, project):
        _post_labor_invoice(pay_client, admin_token, project.id, service_month="2026-07-01", amount=234.56)

        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        data = resp.get_json()

        assert len(data["months"]) == 1
        bucket = data["months"][0]
        assert bucket["workers"] == []
        assert bucket["unassigned_paid"] == 234.56
        assert bucket["unassigned_count"] == 1
        assert bucket["total_paid"] == 234.56

    def test_no_service_month_bucket_last_with_null_year_month(self, pay_client, admin_token, project):
        worker = _create_worker(project.id, "Alice")
        _post_labor_invoice(pay_client, admin_token, project.id, worker_id=str(worker.id), amount=100)  # no month
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(worker.id), service_month="2026-01-01", amount=100
        )
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(worker.id), service_month="2025-12-01", amount=100
        )

        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        data = resp.get_json()

        buckets = [(m["year"], m["month"]) for m in data["months"]]
        assert buckets == [(2026, 1), (2025, 12), (None, None)]

    def test_non_labor_invoices_excluded(self, pay_client, admin_token, project):
        worker = _create_worker(project.id, "Alice")
        _post_labor_invoice(
            pay_client, admin_token, project.id, worker_id=str(worker.id), service_month="2026-07-01", amount=100
        )
        _post_non_labor_invoice(pay_client, admin_token, project.id, amount=9999)

        resp = pay_client.get(_summary_url(project.id), headers=_auth(admin_token))
        data = resp.get_json()

        assert len(data["months"]) == 1
        assert data["months"][0]["total_paid"] == 100.0

    def test_non_member_returns_403(self, pay_client, outsider_token, project):
        resp = pay_client.get(_summary_url(project.id), headers=_auth(outsider_token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Invoice list filters
# ---------------------------------------------------------------------------


class TestInvoiceListLaborPaymentsFilters:
    def test_filter_by_service_month(self, pay_client, admin_token, project):
        worker = _create_worker(project.id, "Alice")
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(worker.id),
            service_month="2026-07-01",
            amount=100,
            notes="july",
        )
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(worker.id),
            service_month="2026-06-01",
            amount=100,
            notes="june",
        )

        resp = pay_client.get(
            _invoices_url(project.id) + "?service_month=2026-07",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        notes = [inv["notes"] for inv in resp.get_json()["invoices"]]
        assert notes == ["july"]

    def test_filter_accepts_full_date_and_normalizes_to_month(self, pay_client, admin_token, project):
        # The FE sends the entity-field convention (YYYY-MM-01) as the filter
        # value; any day suffix must match the whole month, not just day 1.
        worker = _create_worker(project.id, "Alice")
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(worker.id),
            service_month="2026-07-01",
            amount=100,
            notes="july",
        )
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(worker.id),
            service_month="2026-06-01",
            amount=100,
            notes="june",
        )

        for param in ("2026-07-01", "2026-07-15"):
            resp = pay_client.get(
                _invoices_url(project.id) + f"?service_month={param}",
                headers=_auth(admin_token),
            )
            assert resp.status_code == 200, resp.get_data(as_text=True)
            notes = [inv["notes"] for inv in resp.get_json()["invoices"]]
            assert notes == ["july"], param

    def test_filter_by_worker_id(self, pay_client, admin_token, project):
        alice = _create_worker(project.id, "Alice")
        bob = _create_worker(project.id, "Bob")
        _post_labor_invoice(pay_client, admin_token, project.id, worker_id=str(alice.id), amount=100, notes="alice-inv")
        _post_labor_invoice(pay_client, admin_token, project.id, worker_id=str(bob.id), amount=100, notes="bob-inv")

        resp = pay_client.get(
            _invoices_url(project.id) + f"?worker_id={alice.id}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        notes = [inv["notes"] for inv in resp.get_json()["invoices"]]
        assert notes == ["alice-inv"]

    def test_filters_compose_with_type_and_each_other(self, pay_client, admin_token, project):
        alice = _create_worker(project.id, "Alice")
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(alice.id),
            service_month="2026-07-01",
            amount=100,
            notes="match",
        )
        _post_labor_invoice(
            pay_client,
            admin_token,
            project.id,
            worker_id=str(alice.id),
            service_month="2026-06-01",
            amount=100,
            notes="wrong-month",
        )
        _post_non_labor_invoice(pay_client, admin_token, project.id, amount=100)

        resp = pay_client.get(
            _invoices_url(project.id) + f"?type=labor&service_month=2026-07&worker_id={alice.id}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        notes = [inv["notes"] for inv in resp.get_json()["invoices"]]
        assert notes == ["match"]

    def test_invalid_service_month_format_returns_422(self, pay_client, admin_token, project):
        resp = pay_client.get(
            _invoices_url(project.id) + "?service_month=2026-13",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422, resp.get_data(as_text=True)

        resp = pay_client.get(
            _invoices_url(project.id) + "?service_month=not-a-month",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422

    def test_invalid_worker_id_returns_422(self, pay_client, admin_token, project):
        resp = pay_client.get(
            _invoices_url(project.id) + "?worker_id=not-a-uuid",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422, resp.get_data(as_text=True)
