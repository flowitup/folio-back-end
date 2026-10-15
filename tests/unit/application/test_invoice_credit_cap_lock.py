"""Refund and avoir caps sum the credits already booked against a parent invoice.

SQLite cannot show a row lock, so these tests record the order of repository calls: the parent
must be read under its lock (find_by_id_for_update) before any credit is summed, or two
concurrent credit notes could each pass the cap on the same sum.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.invoice.create_invoice import CreateInvoiceRequest, CreateInvoiceUseCase
from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.update_invoice import UpdateInvoiceRequest, UpdateInvoiceUseCase
from app.domain.entities.invoice import Invoice, InvoiceType, SettledVia
from app.domain.value_objects.invoice_item import InvoiceItem


def _invoice(project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, price="100", **overrides) -> Invoice:
    return Invoice(
        id=uuid4(),
        project_id=project_id,
        invoice_number="INV-2026-0001",
        type=invoice_type,
        issue_date=date.today(),
        recipient_name="Supplier",
        recipient_address=None,
        notes=None,
        items=[InvoiceItem(description="Work", quantity=Decimal("1"), unit_price=Decimal(price))],
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        **overrides,
    )


def _recording_repo(parent: Invoice, events: list[str], existing: Invoice | None = None) -> MagicMock:
    repo = MagicMock(spec=IInvoiceRepository)

    def find(invoice_id):
        events.append("find")
        return existing if existing is not None and invoice_id == existing.id else parent

    def lock(invoice_id):
        events.append("lock")
        return parent

    def total(*args, **kwargs):
        events.append("sum")
        return Decimal("0")

    repo.find_by_id.side_effect = find
    repo.find_by_id_for_update.side_effect = lock
    repo.sum_refunds_for_source.side_effect = total
    repo.sum_applied_for_target.side_effect = total
    repo.next_invoice_number.return_value = "INV-2026-0002"
    repo.create.side_effect = lambda inv: inv
    repo.update.side_effect = lambda inv: inv
    return repo


def _create(project_id, **link) -> CreateInvoiceRequest:
    return CreateInvoiceRequest(
        project_id=project_id,
        created_by=uuid4(),
        type=InvoiceType.RETURN,
        issue_date=date.today(),
        recipient_name="Supplier",
        items=[{"description": "Back", "quantity": 1.0, "unit_price": -10.0, "vat_rate": 0.0}],
        **link,
    )


def _assert_locked_before_summing(events: list[str]) -> None:
    assert "sum" in events and "lock" in events
    assert events.index("lock") < events.index("sum")


def test_create_locks_the_refund_source_before_summing_its_returns():
    project_id = uuid4()
    source = _invoice(project_id)
    events: list[str] = []
    CreateInvoiceUseCase(_recording_repo(source, events)).execute(_create(project_id, refunds_invoice_id=source.id))
    _assert_locked_before_summing(events)


def test_create_locks_the_avoir_target_before_summing_what_is_applied_to_it():
    project_id = uuid4()
    target = _invoice(project_id)
    events: list[str] = []
    CreateInvoiceUseCase(_recording_repo(target, events)).execute(
        _create(project_id, settled_via=SettledVia.AVOIR.value, applied_to_invoice_id=target.id)
    )
    _assert_locked_before_summing(events)


def test_update_locks_the_refund_source_before_summing_its_returns():
    project_id = uuid4()
    source = _invoice(project_id)
    returned = _invoice(project_id, InvoiceType.RETURN, price="-10")
    events: list[str] = []
    repo = _recording_repo(source, events, existing=returned)
    UpdateInvoiceUseCase(repo).execute(UpdateInvoiceRequest(invoice_id=returned.id, refunds_invoice_id=source.id))
    _assert_locked_before_summing(events)


def test_update_locks_the_avoir_target_before_summing_what_is_applied_to_it():
    project_id = uuid4()
    target = _invoice(project_id)
    returned = _invoice(project_id, InvoiceType.RETURN, price="-10", settled_via=SettledVia.AVOIR.value)
    events: list[str] = []
    repo = _recording_repo(target, events, existing=returned)
    UpdateInvoiceUseCase(repo).execute(UpdateInvoiceRequest(invoice_id=returned.id, applied_to_invoice_id=target.id))
    _assert_locked_before_summing(events)


def test_the_port_default_reads_without_a_lock_so_other_repositories_keep_working():
    class Repo(IInvoiceRepository):
        def find_by_id(self, invoice_id):
            return ("row", invoice_id)

    Repo.__abstractmethods__ = frozenset()
    assert Repo().find_by_id_for_update(7) == ("row", 7)
