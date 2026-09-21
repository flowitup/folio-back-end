"""S2 — state: builds the context feature C's S3 ``decide_ticket`` Jev call sees.

Every helper here is DB-only (invoice/labor/project repositories) — nothing calls a
provider. ``writable_projects`` is the one place feature C resolves "projects the user
can write invoices for" (D7: no geography table, Jev judges proximity itself from
``address``/``store_city`` already carried by ``Invoice``/``WritableProject``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional
from uuid import UUID

from app.application.assistant.import_ports import InvoiceImportRepositoryPort
from app.application.assistant.models import Invoice
from app.application.authz.ports import AuthzReaderPort
from app.application.invoice.ports import IInvoiceRepository
from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.projects.ports import IProjectRepository
from app.domain.authz.resolver import has_permission
from app.domain.entities.invoice import Invoice as InvoiceEntity, InvoiceType

#: The permission gating "can this user create/attach invoices on this project".
MANAGE_INVOICES_PERMISSION = "project:manage_invoices"

#: How far back "recent purchases from this merchant" looks.
RECENT_PURCHASES_WINDOW_DAYS = 90
#: How wide the duplicate-candidate window is around the ticket's date.
DUPLICATE_WINDOW_DAYS = 30
#: How wide the attach-to-existing-invoice window is around the ticket's date (C3).
MATCH_WINDOW_DAYS = 5
#: Absolute TTC difference tolerated when matching an invoice by amount.
AMOUNT_TOLERANCE = 0.05
#: Absolute HT+TVA vs TTC / lines-sum-vs-total tolerance for the sanity check.
SANITY_TOLERANCE = 0.02
SANITY_LINES_TOLERANCE = 0.5


@dataclass(frozen=True)
class WritableProject:
    """A project the caller may create/attach invoices for."""

    id: UUID
    name: str
    address: Optional[str]


def writable_projects(
    project_repo: IProjectRepository,
    authz_reader: AuthzReaderPort,
    user_id: UUID,
    company_ids: list[UUID],
) -> list[WritableProject]:
    """Projects visible to the user AND granting ``project:manage_invoices``."""
    projects = project_repo.list_for_user_and_companies(user_id, company_ids)
    result: list[WritableProject] = []
    for project in projects:
        if has_permission(authz_reader, user_id, MANAGE_INVOICES_PERMISSION, project_id=project.id):
            result.append(WritableProject(id=project.id, name=project.name, address=project.address))
    return result


def _merchant_matches(recipient_name: Optional[str], merchant: str) -> bool:
    a = (recipient_name or "").strip().lower()
    b = (merchant or "").strip().lower()
    if not a or not b:
        return False
    return b in a or a in b


def workers_on_site(
    labor_entry_repo: ILaborEntryRepository,
    worker_repo: IWorkerRepository,
    project_id: UUID,
    day: date,
) -> list[str]:
    """Names of workers with a labor entry on ``day`` for ``project_id``."""
    entries = labor_entry_repo.list_by_project(project_id, date_from=day, date_to=day)
    names: list[str] = []
    for entry in entries:
        worker = worker_repo.find_by_id(entry.worker_id)
        if worker is not None:
            names.append(worker.person_name or worker.name)
    return names


def recent_purchases(
    invoice_repo: IInvoiceRepository,
    project_id: UUID,
    merchant: str,
    today: date,
) -> list[dict[str, Any]]:
    """Materials/services invoices from ``merchant`` in the last 90 days on this project."""
    date_from = today - timedelta(days=RECENT_PURCHASES_WINDOW_DAYS)
    rows = invoice_repo.find_by_project_in_range(
        project_id, date_from, today, type_filter=InvoiceType.MATERIALS_SERVICES
    )
    hits = [row for row in rows if _merchant_matches(row.recipient_name, merchant)]
    return [
        {"date": row.issue_date.isoformat(), "total_ttc": float(row.total_amount), "invoice_number": row.invoice_number}
        for row in hits
    ]


@dataclass(frozen=True)
class DuplicateCandidate:
    invoice_id: UUID
    project_id: UUID
    project_name: str
    date: str
    total_ttc: float


def duplicate_candidates(
    invoice_repo: IInvoiceRepository,
    projects: list[WritableProject],
    merchant: str,
    total_ttc: float,
    day: date,
) -> list[DuplicateCandidate]:
    """Same merchant, +/- ``AMOUNT_TOLERANCE`` euros, any date within +/- 30 days."""
    date_from = day - timedelta(days=DUPLICATE_WINDOW_DAYS)
    date_to = day + timedelta(days=DUPLICATE_WINDOW_DAYS)
    out: list[DuplicateCandidate] = []
    for project in projects:
        rows = invoice_repo.find_by_project_in_range(
            project.id, date_from, date_to, type_filter=InvoiceType.MATERIALS_SERVICES
        )
        for row in rows:
            if _merchant_matches(row.recipient_name, merchant) and abs(float(row.total_amount) - total_ttc) <= (
                AMOUNT_TOLERANCE
            ):
                out.append(
                    DuplicateCandidate(
                        invoice_id=row.id,
                        project_id=project.id,
                        project_name=project.name,
                        date=row.issue_date.isoformat(),
                        total_ttc=float(row.total_amount),
                    )
                )
    return out


def match_candidates(
    invoice_repo: IInvoiceRepository,
    import_repo: InvoiceImportRepositoryPort,
    projects: list[WritableProject],
    invoice: Invoice,
    day: date,
) -> list[tuple[WritableProject, InvoiceEntity]]:
    """C3: invoices eligible to attach the ticket's scan to (no scan yet)."""
    date_from = day - timedelta(days=MATCH_WINDOW_DAYS)
    date_to = day + timedelta(days=MATCH_WINDOW_DAYS)
    found: list[tuple[WritableProject, InvoiceEntity]] = []
    for project in projects:
        rows = invoice_repo.find_by_project_in_range(
            project.id, date_from, date_to, type_filter=InvoiceType.MATERIALS_SERVICES
        )
        for row in rows:
            if (
                _merchant_matches(row.recipient_name, invoice.merchant)
                and abs(float(row.total_amount) - invoice.total_ttc) <= AMOUNT_TOLERANCE
            ):
                found.append((project, row))
    if not found:
        return []
    available_ids = set(import_repo.list_without_scan_for_invoices([row.id for _, row in found]))
    return [(project, row) for project, row in found if row.id in available_ids]


def amounts_sane(invoice: Invoice) -> bool:
    """HT + TVA ~= TTC, and the sum of line totals ~= TTC (when both are present)."""
    if invoice.total_ht is not None and invoice.total_tva is not None:
        if abs((invoice.total_ht + invoice.total_tva) - invoice.total_ttc) > SANITY_TOLERANCE:
            return False
    line_totals = [line.total_ttc for line in invoice.lines if line.total_ttc is not None]
    if line_totals and abs(sum(line_totals) - invoice.total_ttc) > SANITY_LINES_TOLERANCE:
        return False
    return True


def build_ticket_state(
    invoice: Invoice,
    projects: list[WritableProject],
    workers_by_project: dict[str, list[str]],
    recent_by_project: dict[str, list[dict[str, Any]]],
    duplicates: list[DuplicateCandidate],
    sane: bool,
    chat_hint: Optional[str] = None,
) -> dict[str, Any]:
    """The plain-dict state ``decide.decide_ticket`` hands to Jev's ``system_one``.

    ``chat_hint`` (plan section 3's S3 priority: "chat_hint > reference_field >
    delivery_address > proximité...") is only ever set by feature B — the project name
    or hint the user's chat request already named ("... pour Arcueil"). Feature C's
    photo flow has no such hint and always passes None.
    """
    return {
        "invoice": invoice.model_dump(),
        "chat_hint": chat_hint,
        "projects": [{"id": str(p.id), "name": p.name, "address": p.address} for p in projects],
        "workers_on_site_that_day": workers_by_project,
        "recent_purchases_from_merchant": recent_by_project,
        "duplicate_candidates": [
            {
                "invoice_id": str(c.invoice_id),
                "project_id": str(c.project_id),
                "project_name": c.project_name,
                "date": c.date,
                "total_ttc": c.total_ttc,
            }
            for c in duplicates
        ],
        "amounts_sane": sane,
    }
