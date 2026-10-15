"""SQLAlchemyInvoiceRepository.find_by_id_for_update: the row lock the credit caps rely on."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models.invoice import InvoiceModel
from tests.unit.infrastructure.test_sum_company_spent import _make_company, _make_invoice, _make_project, _make_user


def test_the_statement_asks_postgres_for_a_row_lock(session):
    statements = []

    @event.listens_for(session, "do_orm_execute")
    def capture(state):
        statements.append(state.statement)

    SQLAlchemyInvoiceRepository(session).find_by_id_for_update(uuid4())
    assert "FOR UPDATE" in str(statements[0].compile(dialect=postgresql.dialect()))


def test_it_returns_the_invoice_or_none(session):
    user_id = _make_user(session)
    project_id = _make_project(session, user_id, _make_company(session, user_id))
    invoice_id = _make_invoice(session, project_id, "materials_services", 100.0)
    repo = SQLAlchemyInvoiceRepository(session)
    assert repo.find_by_id_for_update(invoice_id).id == invoice_id
    assert repo.find_by_id_for_update(uuid4()) is None


def test_a_row_loaded_earlier_is_refreshed_after_the_lock(session):
    """The cap compares the source's total, which an edit queued ahead of this request may have changed."""
    user_id = _make_user(session)
    project_id = _make_project(session, user_id, _make_company(session, user_id))
    invoice_id = _make_invoice(session, project_id, "materials_services", 100.0)
    repo = SQLAlchemyInvoiceRepository(session)
    earlier_read = session.get(InvoiceModel, invoice_id)  # the identity map holds rows weakly
    assert repo.find_by_id(invoice_id).total_amount == 100
    session.execute(
        InvoiceModel.__table__.update()
        .where(InvoiceModel.__table__.c.id == invoice_id)
        .values(items=[{"description": "Line", "quantity": 1, "unit_price": 40, "vat_rate": 0}])
    )
    assert earlier_read is not None
    assert repo.find_by_id_for_update(invoice_id).total_amount == 40
