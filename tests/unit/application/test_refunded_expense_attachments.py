"""A refunded expense is locked against edits: its attachments cannot be added, renamed or removed either."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.invoice.manage_attachments import DeleteAttachmentUseCase, RenameAttachmentUseCase
from app.application.invoice.upload_attachment import UploadAttachmentUseCase
from app.domain.entities.invoice_attachment import InvoiceAttachment
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError

PDF = b"%PDF-1.4\n%fake\n"


class _Invoices:
    def __init__(self, refundable_status):
        self.invoice = SimpleNamespace(id=uuid4(), refundable_status=refundable_status)

    def find_by_id(self, invoice_id):
        return self.invoice


class _Attachments:
    def __init__(self, invoice_id):
        self.att = InvoiceAttachment(
            id=uuid4(),
            invoice_id=invoice_id,
            filename="proof.pdf",
            storage_key="k/proof.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            uploaded_at=datetime.now(timezone.utc),
            uploaded_by=uuid4(),
        )
        self.saved, self.deleted, self.renamed = [], [], []

    def find_by_id(self, attachment_id):
        return self.att

    def save(self, attachment):
        self.saved.append(attachment)
        return attachment

    def delete(self, attachment_id):
        self.deleted.append(attachment_id)

    def update_filename(self, attachment_id, new_filename):
        self.renamed.append(new_filename)


class _Storage:
    def __init__(self):
        self.puts, self.deletes = [], []

    def put(self, key, fileobj, content_type):
        self.puts.append(key)

    def delete(self, key):
        self.deletes.append(key)


def _build(refundable_status):
    invoices = _Invoices(refundable_status)
    attachments, storage = _Attachments(invoices.invoice.id), _Storage()
    return invoices, attachments, storage


def _upload(invoices, attachments, storage):
    return UploadAttachmentUseCase(invoices, attachments, storage).execute(
        invoice_id=invoices.invoice.id,
        filename="late.pdf",
        mime_type="application/pdf",
        size_bytes=len(PDF),
        fileobj=io.BytesIO(PDF),
    )


def test_upload_to_a_refunded_expense_is_refused_before_touching_storage():
    invoices, attachments, storage = _build("refunded")
    with pytest.raises(InvalidInvoiceDataError, match="locked"):
        _upload(invoices, attachments, storage)
    assert storage.puts == [] and attachments.saved == []


def test_rename_on_a_refunded_expense_is_refused():
    invoices, attachments, _ = _build("refunded")
    with pytest.raises(InvalidInvoiceDataError, match="locked"):
        RenameAttachmentUseCase(attachments, invoices).execute(attachments.att.id, "other.pdf")
    assert attachments.renamed == []


def test_delete_on_a_refunded_expense_keeps_the_object_and_the_row():
    invoices, attachments, storage = _build("refunded")
    with pytest.raises(InvalidInvoiceDataError, match="locked"):
        DeleteAttachmentUseCase(attachments, storage, invoices).execute(attachments.att.id)
    assert storage.deletes == [] and attachments.deleted == []


@pytest.mark.parametrize("status", [None, "refundable", "not_refundable"])
def test_every_operation_still_works_on_an_expense_that_is_not_refunded(status):
    invoices, attachments, storage = _build(status)
    _upload(invoices, attachments, storage)
    RenameAttachmentUseCase(attachments, invoices).execute(attachments.att.id, "other.pdf")
    DeleteAttachmentUseCase(attachments, storage, invoices).execute(attachments.att.id)
    assert len(storage.puts) == 1 and attachments.renamed == ["other.pdf"] and attachments.deleted


def test_an_invoice_without_a_refund_status_is_not_locked():
    from app.application.invoice.manage_attachments import assert_attachments_editable

    assert_attachments_editable(SimpleNamespace(id=uuid4()))
    assert_attachments_editable(None)
