"""DTOs for invoice export: ExportFormat, InvoiceExportRange, InvoiceExportContext, TypeSubtotal, InvoiceBundle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from app.domain.entities.invoice import Invoice, InvoiceType

# Literal type alias — not a class, so no instantiation
ExportFormat = Literal["xlsx", "pdf"]


@dataclass(frozen=True)
class InvoiceExportRange:
    """Contiguous month range for export (both ends inclusive, first day of month)."""

    from_month: date  # first day of from-month  e.g. date(2026, 1, 1)
    to_month: date  # first day of to-month    e.g. date(2026, 3, 1)


@dataclass(frozen=True)
class InvoiceExportContext:
    """Metadata carried through the invoice export pipeline."""

    project_name: str
    project_id: UUID
    range: InvoiceExportRange
    generated_at: datetime
    generated_by_email: str
    # Optional invoice-type scope — None means export all types
    type_filter: Optional[InvoiceType] = field(default=None)


@dataclass(frozen=True)
class TypeSubtotal:
    """Aggregated totals for a single invoice type."""

    type: InvoiceType
    invoice_count: int
    total_amount: Decimal


@dataclass(frozen=True)
class InvoiceBundle:
    """The full data set passed to builders."""

    invoices: List[Invoice]  # sorted by (issue_date, type, invoice_number)
    subtotals_by_type: List[TypeSubtotal]  # in the order RELEASED_FUNDS, LABOR, MATERIALS_SERVICES (skip empty)
    grand_total: Decimal
    invoice_count: int
