"""Persistence ports for the two AI-import extension tables (feature C/A, phase 03).

``invoice_ai_imports`` and ``assistant_material_imports`` are deliberately kept out of
``app.application.assistant.ports`` — that module holds the provider-facing contracts
from phase 02; these two are pure persistence, implemented together by
``SqlAlchemyAssistantImportRepository`` since both tables only ever matter to the
assistant pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID


@dataclass(frozen=True)
class InvoiceImportRecord:
    """One row of ``invoice_ai_imports`` — the assistant's provenance for an invoice."""

    id: UUID
    invoice_id: UUID
    status: str  # confirmed | to_confirm | needs_review
    source: str  # web | ticket
    ai_confidence: float
    category: Optional[str]
    flags: list[str]
    original_attachment_id: Optional[UUID]
    scan_attachment_id: Optional[UUID]
    trace_id: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MaterialImportRecord:
    """One row of ``assistant_material_imports`` — the assistant's provenance for a product."""

    id: UUID
    product_id: UUID
    status: str  # confirmed | to_confirm
    confidence: float
    photo_sha256: Optional[str]
    source_url: Optional[str]
    created_at: datetime
    updated_at: datetime


class InvoiceImportRepositoryPort(Protocol):
    """Persistence contract for ``invoice_ai_imports`` (feature C).

    Method names are prefixed (``add_invoice_import``/``update_invoice_status``) rather
    than the bare ``add``/``update_status`` the phase brief lists, because
    ``SqlAlchemyAssistantImportRepository`` implements this AND
    ``MaterialImportRepositoryPort`` on one object — Python has no method overloading,
    so two Protocols sharing an object cannot both declare a plain ``add`` with
    different signatures. Prefixing is the smallest change that keeps one repository
    class satisfying both ports.
    """

    def add_invoice_import(
        self,
        *,
        invoice_id: UUID,
        status: str,
        source: str,
        ai_confidence: float,
        category: Optional[str] = None,
        flags: Optional[list[str]] = None,
        original_attachment_id: Optional[UUID] = None,
        scan_attachment_id: Optional[UUID] = None,
        trace_id: Optional[str] = None,
    ) -> InvoiceImportRecord:
        """Insert a new import row for ``invoice_id``."""
        ...

    def find_by_invoice(self, invoice_id: UUID) -> Optional[InvoiceImportRecord]:
        """Return the most recent import row for ``invoice_id``, or None."""
        ...

    def list_without_scan_for_invoices(self, invoice_ids: list[UUID]) -> list[UUID]:
        """Return the subset of ``invoice_ids`` that have no scan attached yet.

        An id qualifies when it has no import row at all, or its most recent import
        row's ``scan_attachment_id`` is NULL. Empty input returns an empty list.
        """
        ...

    def update_invoice_status(self, import_id: UUID, status: str, flags: Optional[list[str]] = None) -> None:
        """Update an import row's status (and optionally its flags list)."""
        ...


class MaterialImportRepositoryPort(Protocol):
    """Persistence contract for ``assistant_material_imports`` (feature A). See the
    ``InvoiceImportRepositoryPort`` docstring for why ``add``/``update_status`` are
    prefixed here too."""

    def add_material_import(
        self,
        *,
        product_id: UUID,
        status: str,
        confidence: float,
        photo_sha256: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> MaterialImportRecord:
        """Insert a new import row for ``product_id``."""
        ...

    def find_by_photo_hash(self, photo_sha256: str) -> Optional[MaterialImportRecord]:
        """Return the import row whose ``photo_sha256`` matches, or None (cache hit)."""
        ...

    def find_by_reference(self, company_id: UUID, reference: str) -> Optional[MaterialImportRecord]:
        """Return the import row of an existing product with this supplier reference.

        Scoped to ``company_id`` (joins ``bibliotheque_products``); ``reference`` is
        matched case-insensitively against ``bibliotheque_products.supplier_reference``.
        """
        ...

    def update_material_status(self, import_id: UUID, status: str) -> None:
        """Update an import row's status."""
        ...


__all__ = [
    "InvoiceImportRecord",
    "MaterialImportRecord",
    "InvoiceImportRepositoryPort",
    "MaterialImportRepositoryPort",
]
