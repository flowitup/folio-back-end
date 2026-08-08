"""Tests for SQLAlchemyInvoiceRepository.get_labor_payments_summary.

Exercises the repo-level aggregation directly against SQLite (via the shared
`session` fixture), the same pattern as tests/test_labor_repository.py — no
Flask app / JWT needed since this is a pure read aggregation.

Covers: multi-month multi-worker aggregation, unassigned bucket (NULL
worker_id — same shape whether never-linked or SET-NULLed by a worker
delete), no-month bucket ordering (always last), non-labor exclusion,
project isolation, and empty project.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models import PersonModel, ProjectModel, UserModel, WorkerModel
from app.infrastructure.database.models.invoice import InvoiceModel


def _items(*, quantity=1, unit_price=100, vat_rate=0):
    """A single-line JSONB items payload; items_total(items) == quantity * unit_price."""
    return [{"description": "Labor", "quantity": quantity, "unit_price": unit_price, "vat_rate": vat_rate}]


@pytest.fixture
def owner_user(session):
    user = UserModel(id=uuid4(), email="payments_owner@test.com", password_hash="hashed", is_active=True)
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def project(session, owner_user):
    p = ProjectModel(id=uuid4(), name="Payments Summary Project", owner_id=owner_user.id)
    session.add(p)
    session.commit()
    return p


@pytest.fixture
def other_project(session, owner_user):
    p = ProjectModel(id=uuid4(), name="Other Project", owner_id=owner_user.id)
    session.add(p)
    session.commit()
    return p


@pytest.fixture
def repo(session):
    return SQLAlchemyInvoiceRepository(session)


def _make_invoice(
    session,
    *,
    project_id,
    invoice_number,
    worker_id=None,
    service_month=None,
    items=None,
    invoice_type="labor",
    created_by=None,
):
    now = datetime.now(timezone.utc)
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=invoice_number,
        type=invoice_type,
        issue_date=date(2026, 1, 1),
        recipient_name="Payroll",
        items=items or _items(),
        created_by=created_by,
        created_at=now,
        updated_at=now,
        service_month=service_month,
        worker_id=worker_id,
    )
    session.add(inv)
    return inv


class TestLaborPaymentsSummaryAggregation:
    def test_empty_project_returns_no_buckets(self, repo, project):
        assert repo.get_labor_payments_summary(project.id) == []

    def test_multi_month_multi_worker_aggregation(self, session, repo, project):
        worker_a = WorkerModel(id=uuid4(), project_id=project.id, name="Alice", daily_rate=Decimal("100"))
        worker_b = WorkerModel(id=uuid4(), project_id=project.id, name="Bob", daily_rate=Decimal("120"))
        session.add_all([worker_a, worker_b])
        session.flush()

        # July: 2 invoices for Alice (summed + counted), 1 for Bob.
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-1",
            worker_id=worker_a.id,
            service_month=date(2026, 7, 1),
            items=_items(unit_price=500),
        )
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-2",
            worker_id=worker_a.id,
            service_month=date(2026, 7, 1),
            items=_items(unit_price=300),
        )
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-3",
            worker_id=worker_b.id,
            service_month=date(2026, 7, 1),
            items=_items(unit_price=400),
        )
        # June: Bob only.
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-4",
            worker_id=worker_b.id,
            service_month=date(2026, 6, 1),
            items=_items(unit_price=250),
        )
        session.commit()

        result = repo.get_labor_payments_summary(project.id)

        assert [(r.year, r.month) for r in result] == [(2026, 7), (2026, 6)]

        july = result[0]
        assert july.unassigned_paid == Decimal("0")
        assert july.unassigned_count == 0
        by_worker = {w.worker_id: w for w in july.workers}
        assert by_worker[worker_a.id].paid == Decimal("800")
        assert by_worker[worker_a.id].invoice_count == 2
        assert by_worker[worker_a.id].worker_name == "Alice"
        assert by_worker[worker_b.id].paid == Decimal("400")
        assert by_worker[worker_b.id].invoice_count == 1

        june = result[1]
        assert len(june.workers) == 1
        assert june.workers[0].worker_id == worker_b.id
        assert june.workers[0].paid == Decimal("250")

    def test_worker_name_prefers_linked_person(self, session, repo, project, owner_user):
        person = PersonModel(
            id=uuid4(),
            name="Jean Dupont",
            normalized_name="jean dupont",
            created_by_user_id=owner_user.id,
        )
        session.add(person)
        session.flush()

        worker = WorkerModel(
            id=uuid4(), project_id=project.id, person_id=person.id, name="Legacy Name", daily_rate=Decimal("100")
        )
        session.add(worker)
        session.flush()

        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-PERSON",
            worker_id=worker.id,
            service_month=date(2026, 8, 1),
        )
        session.commit()

        result = repo.get_labor_payments_summary(project.id)
        assert result[0].workers[0].worker_name == "Jean Dupont"

    def test_null_worker_id_rolls_into_unassigned(self, session, repo, project):
        """Covers both 'never linked' and 'linked worker later deleted' (SET NULL) —
        the repo cannot distinguish them and neither should the response."""
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-UNASSIGNED-1",
            worker_id=None,
            service_month=date(2026, 7, 1),
            items=_items(unit_price=234.56),
        )
        session.commit()

        result = repo.get_labor_payments_summary(project.id)
        assert len(result) == 1
        bucket = result[0]
        assert bucket.workers == []
        assert bucket.unassigned_paid == Decimal("234.56")
        assert bucket.unassigned_count == 1

    def test_no_service_month_bucket_ordered_last(self, session, repo, project):
        worker = WorkerModel(id=uuid4(), project_id=project.id, name="Alice", daily_rate=Decimal("100"))
        session.add(worker)
        session.flush()

        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-NO-MONTH",
            worker_id=worker.id,
            service_month=None,
            items=_items(unit_price=100),
        )
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-JAN",
            worker_id=worker.id,
            service_month=date(2026, 1, 1),
            items=_items(unit_price=100),
        )
        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-DEC",
            worker_id=worker.id,
            service_month=date(2025, 12, 1),
            items=_items(unit_price=100),
        )
        session.commit()

        result = repo.get_labor_payments_summary(project.id)

        # Most-recent month first, no-month bucket always last regardless of magnitude.
        assert [(r.year, r.month) for r in result] == [(2026, 1), (2025, 12), (None, None)]
        assert result[-1].year is None and result[-1].month is None

    def test_non_labor_invoices_excluded(self, session, repo, project):
        worker = WorkerModel(id=uuid4(), project_id=project.id, name="Alice", daily_rate=Decimal("100"))
        session.add(worker)
        session.flush()

        _make_invoice(
            session,
            project_id=project.id,
            invoice_number="INV-MS",
            worker_id=None,
            service_month=None,
            invoice_type="materials_services",
            items=_items(unit_price=999),
        )
        session.commit()

        assert repo.get_labor_payments_summary(project.id) == []

    def test_project_isolation(self, session, repo, project, other_project):
        worker = WorkerModel(id=uuid4(), project_id=other_project.id, name="Other Worker", daily_rate=Decimal("100"))
        session.add(worker)
        session.flush()

        _make_invoice(
            session,
            project_id=other_project.id,
            invoice_number="INV-OTHER",
            worker_id=worker.id,
            service_month=date(2026, 7, 1),
        )
        session.commit()

        assert repo.get_labor_payments_summary(project.id) == []
