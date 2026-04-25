"""Invoice repository port — persistence contract for the invoice domain."""

from abc import ABC, abstractmethod
from typing import Optional
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
        self, project_id: UUID, invoice_type: Optional[InvoiceType] = None
    ) -> list[Invoice]: ...

    @abstractmethod
    def update(self, invoice: Invoice) -> Invoice: ...

    @abstractmethod
    def delete(self, invoice_id: UUID) -> bool: ...

    @abstractmethod
    def next_invoice_number(self, project_id: UUID) -> str: ...
