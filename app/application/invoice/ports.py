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
    def list_by_project(
        self, project_id: UUID, invoice_type: Optional[InvoiceType] = None, tag_id: Optional[UUID] = None
    ) -> list[Invoice]: ...

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

    @abstractmethod
    def list_materials_services_by_companies(
        self,
        company_ids: list[UUID],
        refundable: Optional[bool],
        limit: int,
        offset: int,
        all_companies: bool = False,
    ) -> tuple[list[dict], int]:
        """Return paginated materials_services invoices across projects of company_ids.

        Each row dict includes all Invoice fields plus:
          - 'project_name' (string): resolved via JOIN, no N+1.
          - 'attachments' (list[dict]): each dict has id, filename, mime_type, size_bytes.
            Loaded in a single batch query (one IN clause) over the page's invoice ids.
            Empty list when no attachments exist for that invoice.

        all_companies=True skips the company_ids filter (superadmin cross-company view).
        refundable=True  → only rows where refundable_status IS NOT NULL
        refundable=False → only rows where refundable_status IS NULL
        refundable=None  → no status filter

        Ordered by issue_date DESC, created_at DESC.
        Returns (rows, total_count).
        """
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
    def update_filename(self, attachment_id: UUID, new_filename: str) -> bool: ...

    @abstractmethod
    def delete(self, attachment_id: UUID) -> bool: ...
