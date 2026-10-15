"""Refund and avoir caps sum the credits already booked against a parent invoice.

SQLite cannot show a row lock, so these tests record the order of repository calls: the parent
must be read under its lock (find_by_id_for_update) before any credit is summed, or two
concurrent credit notes could each pass the cap on the same sum.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

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


def _two_invoices(project_id) -> tuple[Invoice, Invoice]:
    """(low, high): two supplier invoices of one project, ordered by the string of their id."""
    a, b = _invoice(project_id), _invoice(project_id)
    return (a, b) if str(a.id) < str(b.id) else (b, a)


class LockRecorder:
    """Repository fake that records which invoice rows are locked, in what order."""

    def __init__(self, *rows: Invoice) -> None:
        self.rows = {r.id: r for r in rows}
        self.locked: list[UUID] = []
        self.repo = MagicMock(spec=IInvoiceRepository)
        self.repo.find_by_id.side_effect = lambda i: self.rows.get(i)
        self.repo.find_by_id_for_update.side_effect = self._lock
        self.repo.sum_refunds_for_source.return_value = Decimal("0")
        self.repo.sum_applied_for_target.return_value = Decimal("0")
        self.repo.next_invoice_number.return_value = "INV-2026-0002"
        self.repo.create.side_effect = lambda inv: inv
        self.repo.update.side_effect = lambda inv: inv

    def _lock(self, invoice_id):
        self.locked.append(invoice_id)
        return self.rows.get(invoice_id)


@pytest.mark.parametrize("source_is_low", [True, False])
def test_create_locks_the_refund_source_and_the_avoir_target_in_id_order(source_is_low):
    """Two returns that cross (R1 refunds S applied to T, R2 refunds T applied to S) must lock S and T alike."""
    project_id = uuid4()
    low, high = _two_invoices(project_id)
    source, target = (low, high) if source_is_low else (high, low)
    rec = LockRecorder(low, high)
    CreateInvoiceUseCase(rec.repo).execute(
        _create(
            project_id,
            refunds_invoice_id=source.id,
            settled_via=SettledVia.AVOIR.value,
            applied_to_invoice_id=target.id,
        )
    )
    assert rec.locked == [low.id, high.id]


@pytest.mark.parametrize("source_is_low", [True, False])
def test_update_locks_the_refund_source_and_the_avoir_target_in_id_order(source_is_low):
    project_id = uuid4()
    low, high = _two_invoices(project_id)
    source, target = (low, high) if source_is_low else (high, low)
    returned = _invoice(project_id, InvoiceType.RETURN, price="-10", settled_via=SettledVia.AVOIR.value)
    rec = LockRecorder(low, high, returned)
    UpdateInvoiceUseCase(rec.repo).execute(
        UpdateInvoiceRequest(invoice_id=returned.id, refunds_invoice_id=source.id, applied_to_invoice_id=target.id)
    )
    assert rec.locked == [low.id, high.id]


@pytest.mark.parametrize("source_is_low", [True, False])
def test_update_of_the_stored_links_locks_them_in_id_order_too(source_is_low):
    """Changing only the lines re-checks both caps against the stored source and target."""
    project_id = uuid4()
    low, high = _two_invoices(project_id)
    source, target = (low, high) if source_is_low else (high, low)
    returned = _invoice(
        project_id,
        InvoiceType.RETURN,
        price="-10",
        settled_via=SettledVia.AVOIR.value,
        refunds_invoice_id=source.id,
        applied_to_invoice_id=target.id,
    )
    rec = LockRecorder(low, high, returned)
    items = [{"description": "Back", "quantity": 1.0, "unit_price": -20.0, "vat_rate": 0.0}]
    UpdateInvoiceUseCase(rec.repo).execute(UpdateInvoiceRequest(invoice_id=returned.id, items=items))
    assert rec.locked == [low.id, high.id]


def test_a_return_linked_to_one_invoice_twice_locks_it_once():
    project_id = uuid4()
    both = _invoice(project_id)
    rec = LockRecorder(both)
    CreateInvoiceUseCase(rec.repo).execute(
        _create(
            project_id, refunds_invoice_id=both.id, settled_via=SettledVia.AVOIR.value, applied_to_invoice_id=both.id
        )
    )
    assert rec.locked == [both.id]


def test_the_port_default_reads_without_a_lock_so_other_repositories_keep_working():
    class Repo(IInvoiceRepository):
        def find_by_id(self, invoice_id):
            return ("row", invoice_id)

    Repo.__abstractmethods__ = frozenset()
    assert Repo().find_by_id_for_update(7) == ("row", 7)
