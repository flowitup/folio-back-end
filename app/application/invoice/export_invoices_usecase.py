"""ExportInvoicesUseCase — orchestrates project + invoice loading → file bytes."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.invoice.ports import IInvoiceRepository
from app.application.projects.ports import IProjectRepository
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.project_exceptions import ProjectNotFoundError
from app.domain.invoice.export.models import (
    ExportFormat,
    InvoiceBundle,
    InvoiceExportContext,
    InvoiceExportRange,
    TypeSubtotal,
)

_TYPE_ORDER = (InvoiceType.CLIENT, InvoiceType.LABOR, InvoiceType.SUPPLIER)


@dataclass
class ExportInvoicesRequest:
    """Input DTO for the invoice export use case."""

    project_id: UUID
    from_month: str  # "YYYY-MM"  e.g. "2026-01"
    to_month: str  # "YYYY-MM"  e.g. "2026-03"
    format: ExportFormat  # "xlsx" | "pdf"
    acting_user_email: str
    type_filter: Optional[InvoiceType] = field(default=None)


@dataclass
class ExportInvoicesResult:
    """Output DTO returned to the API layer."""

    content: bytes
    filename: str  # e.g. "invoices-downtown-office-tower-2026-01-to-2026-03.xlsx"
    mime_type: str  # e.g. "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_yyyy_mm(s: str) -> date:
    """Parse 'YYYY-MM' string to date with day=1."""
    return date(int(s[:4]), int(s[5:7]), 1)


def _last_of_month(d: date) -> date:
    """Return last calendar day of the month for the given date."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


class ExportInvoicesUseCase:
    """Orchestrate invoice loading for a date range into a downloadable file.

    Permission check is delegated to the route layer (@require_permission("project:read")).
    This use-case only validates project existence.
    """

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        project_repo: IProjectRepository,
    ) -> None:
        self._invoice_repo = invoice_repo
        self._project_repo = project_repo

    def execute(self, req: ExportInvoicesRequest) -> ExportInvoicesResult:
        """Generate export file.

        Args:
            req: ExportInvoicesRequest with project_id, from_month, to_month, format,
                 acting_user_email, and optional type_filter.

        Returns:
            ExportInvoicesResult with raw bytes, filename, and MIME type.

        Raises:
            ProjectNotFoundError: if project does not exist.
        """
        # 1. Resolve project — raises ProjectNotFoundError if absent
        project = self._project_repo.find_by_id(req.project_id)
        if project is None:
            raise ProjectNotFoundError(str(req.project_id))

        # 2. Parse month boundaries
        from_d = _parse_yyyy_mm(req.from_month)
        to_d = _last_of_month(_parse_yyyy_mm(req.to_month))

        # 3. Load invoices in range, optionally filtered by type
        invoices: list[Invoice] = self._invoice_repo.find_by_project_in_range(
            project_id=req.project_id,
            date_from=from_d,
            date_to=to_d,
            type_filter=req.type_filter,
        )

        # 4. Sort deterministically: (issue_date, type.value, invoice_number)
        invoices.sort(key=lambda inv: (inv.issue_date, inv.type.value, inv.invoice_number))

        # 5. Aggregate per-type subtotals + grand total (Decimal-safe)
        subtotals: list[TypeSubtotal] = []
        for t in _TYPE_ORDER:
            scoped = [i for i in invoices if i.type == t]
            if not scoped:
                continue
            subtotals.append(
                TypeSubtotal(
                    type=t,
                    invoice_count=len(scoped),
                    total_amount=sum((i.total_amount for i in scoped), Decimal("0")),
                )
            )
        grand_total = sum((s.total_amount for s in subtotals), Decimal("0"))

        # 6. Build bundle + context
        bundle = InvoiceBundle(
            invoices=invoices,
            subtotals_by_type=subtotals,
            grand_total=grand_total,
            invoice_count=len(invoices),
        )
        context = InvoiceExportContext(
            project_name=project.name,
            project_id=req.project_id,
            range=InvoiceExportRange(
                from_month=from_d.replace(day=1),
                to_month=to_d.replace(day=1),
            ),
            generated_at=datetime.now(timezone.utc),
            generated_by_email=req.acting_user_email,
            type_filter=req.type_filter,
        )

        # 7. Dispatch to builder
        from app.domain.invoice.export.format import slugify_project_name

        if req.format == "xlsx":
            from app.domain.invoice.export.xlsx_builder import build_xlsx

            content = build_xlsx(context, bundle)
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        elif req.format == "pdf":
            from app.domain.invoice.export.pdf_builder import build_pdf

            content = build_pdf(context, bundle)
            mime_type = "application/pdf"
            ext = "pdf"
        else:
            raise ValueError(f"Unsupported export format: {req.format!r}")

        # 8. Generate filename
        slug = slugify_project_name(project.name, str(project.id))
        type_suffix = f"-{req.type_filter.value}" if req.type_filter else ""
        filename = f"invoices-{slug}-{req.from_month}-to-{req.to_month}{type_suffix}.{ext}"

        return ExportInvoicesResult(content=content, filename=filename, mime_type=mime_type)
