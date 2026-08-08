"""Integration test: invoices.worker_id FK enforces ON DELETE SET NULL on worker delete.

Uses a dedicated in-memory SQLite engine with PRAGMA foreign_keys=ON. The shared
conftest engine/session fixtures do NOT enable FK enforcement (other unit tests
rely on that to insert rows out of order within one transaction), so this test
is fully self-contained rather than touching that shared fixture.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.infrastructure.database.models import Base
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.worker import WorkerModel


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def fk_enforced_session():
    """A standalone SQLite session with real FK-constraint enforcement (ON DELETE etc.)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session

    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_deleting_worker_sets_invoice_worker_id_null(fk_enforced_session):
    session = fk_enforced_session

    user = UserModel(id=uuid4(), email="fk_test@test.com", password_hash="x", is_active=True)
    session.add(user)
    session.flush()

    company = CompanyModel(
        id=uuid4(),
        legal_name="FK Test Co",
        address="1 rue",
        created_by=user.id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(company)
    session.flush()

    project = ProjectModel(id=uuid4(), name="FK Test Project", owner_id=user.id, company_id=company.id)
    session.add(project)
    session.flush()

    worker = WorkerModel(id=uuid4(), project_id=project.id, name="Linked Worker", daily_rate=Decimal("100.00"))
    session.add(worker)
    session.flush()

    invoice = InvoiceModel(
        id=uuid4(),
        project_id=project.id,
        invoice_number="INV-2026-0001",
        type="labor",
        issue_date=date.today(),
        recipient_name="Linked Worker",
        items=[],
        created_by=user.id,
        created_at=_now(),
        updated_at=_now(),
        worker_id=worker.id,
    )
    session.add(invoice)
    session.commit()

    session.delete(worker)
    session.commit()

    session.expire_all()
    reloaded = session.get(InvoiceModel, invoice.id)
    assert reloaded is not None
    assert reloaded.worker_id is None
