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
from app.domain.entities.project import project_display_label
from app.domain.exceptions.project_exceptions import ProjectNotFoundError
from app.domain.invoice.export.format import round_cents
from app.domain.invoice.export.models import (
    ExportFormat,
    InvoiceBundle,
    InvoiceExportContext,
    InvoiceExportRange,
    TypeSubtotal,
)

# Subtotal rows in order, as (ledger type, cash advance). The expense rows come
# first, so they sit right above the expenses total they add up to; the funding
# rows (released funds, and cash advances, which are listed under Others but count
# in no total — see grand_total below) follow, marked as left out of it.
_SUBTOTAL_ROWS = (
    (InvoiceType.LABOR, False),
    (InvoiceType.MATERIALS_SERVICES, False),
    (InvoiceType.OTHERS, False),
    (InvoiceType.RETURN, False),
    (InvoiceType.RELEASED_FUNDS, False),
    (InvoiceType.OTHERS, True),
)


@dataclass
class ExportInvoicesRequest:
    """Input DTO for the invoice export use case."""

    project_id: UUID
    from_month: str  # "YYYY-MM"  e.g. "2026-01"
    to_month: str  # "YYYY-MM"  e.g. "2026-03"
    format: ExportFormat  # "xlsx" | "pdf"
    acting_user_email: str
    type_filter: Optional[InvoiceType] = field(default=None)
    # Types the caller is not allowed to read at all (currently released_funds
    # without `project:view_budget`). Applied after the range query, so the
    # subtotals and grand total below are computed on what the caller can see.
    exclude_types: frozenset = field(default_factory=frozenset)
    # Label language of the file: "en" (default), "fr" or "vi".
    locale: str = "en"


@dataclass
class ExportInvoicesResult:
    """Output DTO returned to the API layer."""

    content: bytes
    filename: str  # e.g. "invoices-downtown-office-tower-2026-01-to-2026-03.xlsx"
    mime_type: str  # e.g. "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_yyyy_mm(s: str) -> date:
    """Parse 'YYYY-MM' string to date with day=1."""
    return date(int(s[:4]), int(s[5:7]), 1)


def _effective_month(inv: Invoice) -> date:
    """The month an expense is filed under: a labor payment's payment month, else its issue month."""
    if inv.type == InvoiceType.LABOR and inv.service_month is not None:
        return inv.service_month.replace(day=1)
    return inv.issue_date.replace(day=1)


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

        # 3. Load invoices in range. The type filter matches the LEDGER type, not the
        # stored one — a cash advance is stored as released_funds but listed under
        # others — so it is applied here rather than in the repository query.
        invoices: list[Invoice] = self._invoice_repo.find_by_project_in_range(
            project_id=req.project_id,
            date_from=from_d,
            date_to=to_d,
            type_filter=None,
            # Same month as the expense list: labor by its payment month.
            by_payment_month=True,
        )
        if req.type_filter is not None:
            invoices = [i for i in invoices if i.ledger_type == req.type_filter]
        # Permission exclusion reads the STORED type: a cash advance is financing
        # data, hidden from callers without project:view_budget like any release.
        if req.exclude_types:
            invoices = [i for i in invoices if i.type not in req.exclude_types]

        # 4. Sort deterministically: (effective month, issue_date, ledger type, invoice_number)
        invoices.sort(
            key=lambda inv: (_effective_month(inv), inv.issue_date, inv.ledger_type.value, inv.invoice_number)
        )

        # 5. Aggregate per-type subtotals + totals (Decimal-safe). Each row counts at its
        # total rounded to the cent, the amount the app shows and sums, so the file's
        # totals match the app's to the cent.
        subtotals: list[TypeSubtotal] = []
        for t, cash_advance in _SUBTOTAL_ROWS:
            scoped = [i for i in invoices if i.ledger_type == t and i.is_cash_advance == cash_advance]
            if not scoped:
                continue
            subtotals.append(
                TypeSubtotal(
                    type=t,
                    invoice_count=len(scoped),
                    total_amount=sum((round_cents(i.total_amount) for i in scoped), Decimal("0")),
                    is_cash_advance=cash_advance,
                )
            )
        # Money in and money out are never added together. The total is what the
        # project spent: every row but the funds released to it, with returns
        # (negative) netted in. A cash advance is stored as a release and, as in
        # the app, counts in neither total.
        grand_total = sum(
            (round_cents(i.total_amount) for i in invoices if i.type != InvoiceType.RELEASED_FUNDS),
            Decimal("0"),
        )
        released_total = sum(
            (
                round_cents(i.total_amount)
                for i in invoices
                if i.type == InvoiceType.RELEASED_FUNDS and not i.is_cash_advance
            ),
            Decimal("0"),
        )

        # 6. Build bundle + context
        bundle = InvoiceBundle(
            invoices=invoices,
            subtotals_by_type=subtotals,
            grand_total=grand_total,
            invoice_count=len(invoices),
            released_total=released_total,
        )
        # Projects are shown by their site address everywhere else; the stored name
        # is only the fallback (project_display_label), here as in the file name.
        project_label = project_display_label(project)
        context = InvoiceExportContext(
            project_name=project_label,
            project_id=req.project_id,
            range=InvoiceExportRange(
                from_month=from_d.replace(day=1),
                to_month=to_d.replace(day=1),
            ),
            generated_at=datetime.now(timezone.utc),
            generated_by_email=req.acting_user_email,
            type_filter=req.type_filter,
            locale=req.locale,
        )

        # 7. Dispatch to builder
        from app.domain.invoice.export.format import slugify_project_name
        from app.domain.invoice.export.labels import t as label

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
        slug = slugify_project_name(project_label, str(project.id))
        type_suffix = f"-{req.type_filter.value}" if req.type_filter else ""
        prefix = label(req.locale, "file_prefix")
        filename = f"{prefix}-{slug}-{req.from_month}-to-{req.to_month}{type_suffix}.{ext}"

        return ExportInvoicesResult(content=content, filename=filename, mime_type=mime_type)
