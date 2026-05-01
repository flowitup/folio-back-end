"""Invoice repository port — persistence contract for the invoice domain."""

from abc import ABC, abstractmethod
from datetime import date
from typing import BinaryIO, Optional
from uuid import UUID

from app.domain.entities.invoice import Invoice, InvoiceType


class IInvoiceRepository(ABC):
    """Port defining the invoice persistence contract."""

    @abstractmethod
    def create(self, invoice: Invoice) -> Invoice: ...

    @abstractmethod
    def find_by_id(self, invoice_id: UUID) -> Optional[Invoice]: ...

    @abstractmethod
    def list_by_project(self, project_id: UUID, invoice_type: Optional[InvoiceType] = None) -> list[Invoice]: ...

    @abstractmethod
    def update(self, invoice: Invoice) -> Invoice: ...

    @abstractmethod
    def delete(self, invoice_id: UUID) -> bool: ...

    @abstractmethod
    def next_invoice_number(self, project_id: UUID) -> str: ...

    @abstractmethod
    def find_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
        type_filter: Optional[InvoiceType] = None,
    ) -> list[Invoice]:
        """Return invoices for the project where issue_date ∈ [date_from, date_to],
        optionally filtered by type. Returns [] if none."""
        ...


class IAttachmentStorage(ABC):
    """Port for binary file storage (S3 / MinIO / local FS)."""

    @abstractmethod
    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        """Upload a file. `key` is the storage object path."""

    @abstractmethod
    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        """Open a download stream. Returns (file-like, content_length)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove an object. Idempotent — no-op if key does not exist."""


class IInvoiceAttachmentRepository(ABC):
    """Port for invoice attachment metadata persistence."""

    @abstractmethod
    def save(self, attachment) -> "InvoiceAttachment":  # noqa: F821 — fwd ref
        ...

    @abstractmethod
    def find_by_id(self, attachment_id: UUID): ...

    @abstractmethod
    def list_by_invoice(self, invoice_id: UUID) -> list: ...

    @abstractmethod
    def delete(self, attachment_id: UUID) -> bool: ...
