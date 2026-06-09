"""Unit tests for RenameAttachmentUseCase."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.application.invoice.manage_attachments import (
    AttachmentNotFoundError,
    RenameAttachmentUseCase,
)
from app.application.invoice.ports import IInvoiceAttachmentRepository
from app.domain.entities.invoice_attachment import InvoiceAttachment


def make_attachment(filename: str = "receipt.pdf") -> InvoiceAttachment:
    return InvoiceAttachment(
        id=uuid4(),
        invoice_id=uuid4(),
        filename=filename,
        storage_key="invoices/abc/receipt.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        uploaded_at=datetime.now(timezone.utc),
        uploaded_by=uuid4(),
    )


class FakeAttachmentRepo(IInvoiceAttachmentRepository):
    """In-memory repo that mutates filename in place, mirroring SQLAlchemy adapter."""

    def __init__(self, attachment: InvoiceAttachment | None):
        self._att = attachment
        self.update_calls: list[tuple] = []

    def save(self, attachment):  # pragma: no cover — unused here
        raise NotImplementedError

    def find_by_id(self, attachment_id):
        if self._att and self._att.id == attachment_id:
            return self._att
        return None

    def list_by_invoice(self, invoice_id):  # pragma: no cover — unused here
        return []

    def update_filename(self, attachment_id, new_filename):
        self.update_calls.append((attachment_id, new_filename))
        if self._att and self._att.id == attachment_id:
            self._att.filename = new_filename
            return True
        return False

    def delete(self, attachment_id):  # pragma: no cover — unused here
        return False


class TestRenameAttachmentSuccess:
    def test_renames_preserving_extension(self):
        att = make_attachment("old-receipt.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        result = uc.execute(att.id, "Invoice March 2026.pdf")

        assert result.filename == "Invoice March 2026.pdf"
        assert repo.update_calls == [(att.id, "Invoice March 2026.pdf")]

    def test_strips_whitespace(self):
        att = make_attachment("old.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        result = uc.execute(att.id, "  spaced.pdf  ")

        assert result.filename == "spaced.pdf"

    def test_accepts_case_insensitive_extension(self):
        # Stored as .pdf; user types .PDF — treated as unchanged extension.
        att = make_attachment("scan.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        result = uc.execute(att.id, "Scan Final.PDF")

        assert result.filename == "Scan Final.PDF"

    def test_renames_image_attachment(self):
        att = make_attachment("photo.jpeg")
        att.mime_type = "image/jpeg"
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        result = uc.execute(att.id, "site-photo.jpeg")

        assert result.filename == "site-photo.jpeg"


class TestRenameAttachmentValidation:
    def test_rejects_extension_change(self):
        att = make_attachment("receipt.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        with pytest.raises(ValueError, match="extension must remain .pdf"):
            uc.execute(att.id, "receipt.txt")
        assert repo.update_calls == []  # not persisted

    def test_rejects_dropping_extension(self):
        att = make_attachment("receipt.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        with pytest.raises(ValueError):
            uc.execute(att.id, "receipt")

    def test_rejects_empty_filename(self):
        att = make_attachment("receipt.pdf")
        repo = FakeAttachmentRepo(att)
        uc = RenameAttachmentUseCase(repo)

        with pytest.raises(ValueError, match="cannot be empty"):
            uc.execute(att.id, "   ")
        assert repo.update_calls == []


class TestRenameAttachmentNotFound:
    def test_raises_when_missing(self):
        repo = FakeAttachmentRepo(None)
        uc = RenameAttachmentUseCase(repo)

        with pytest.raises(AttachmentNotFoundError):
            uc.execute(uuid4(), "whatever.pdf")
