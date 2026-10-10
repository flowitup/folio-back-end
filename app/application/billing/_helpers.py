"""Shared helpers for the billing application layer.

Private module — only imported by use-cases in this package.
No Flask / SQLAlchemy / infrastructure imports allowed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import DevisLockedByFactureError, ForbiddenBillingDocumentError
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.value_objects import BillingDocumentItem
from app.domain.companies.company import Company
from app.domain.companies.roles import CompanyRole
from app.domain.time import business_today
from app.application.billing.dtos import ItemInput

_DEFAULT_VALIDITY_DAYS = 30  # devis
_DEFAULT_PAYMENT_DAYS = 30  # facture


def _funds_release_items(doc: BillingDocument) -> list[dict]:
    """A facture's lines as released_funds items: one per line, each with its own
    vat_rate, so the release's TTC total matches the facture's.

    The facture rounds a line's HT to the cent before its TVA, while an expense line
    rounds only its TTC. Both agree when quantity × unit_price is already at the cent,
    so such a line keeps its quantity and unit price; any other line becomes one unit
    at its rounded HT (2.5 × 19.99 → 1 × 49.98), else the release would be a cent off.
    """
    items = []
    for it in doc.items:
        quantity, unit_price = it.quantity, it.unit_price
        if quantity * unit_price != it.total_ht:
            quantity, unit_price = Decimal("1"), it.total_ht
        items.append(
            {
                "description": it.description,
                "quantity": str(quantity),
                "unit_price": str(unit_price),
                "vat_rate": str(it.vat_rate),
            }
        )
    return items


def _assert_owner(doc: BillingDocument, user_id: UUID) -> None:
    """Raise ForbiddenBillingDocumentError if user_id does not own doc."""
    if doc.user_id != user_id:
        raise ForbiddenBillingDocumentError(doc.id)


def _assert_billing_doc_access(doc: BillingDocument, user_id: UUID, access_repo=None) -> None:
    """Read access: a company-admin of the doc's company, or its author while still
    attached to that company (a demoted author keeps seeing what they wrote; a
    removed one does not). A document with no company stays private to its author.

    Raises ForbiddenBillingDocumentError otherwise. ``access_repo`` is the
    UserCompanyAccessRepositoryPort; when None (legacy/test wiring) the check
    degrades to owner-only — never widens access by accident.
    """
    if access_repo is None or doc.company_id is None:
        if doc.user_id == user_id:
            return
        raise ForbiddenBillingDocumentError(doc.id)
    access = access_repo.find(user_id, doc.company_id)
    if access is not None and (access.role == CompanyRole.ADMIN.value or doc.user_id == user_id):
        return
    raise ForbiddenBillingDocumentError(doc.id)


def _assert_billing_doc_write_access(doc: BillingDocument, user_id: UUID, access_repo=None) -> None:
    """Changes (edit, status, delete, convert) to a company document need the
    company-admin role, the same rule as creating one: authorship alone is not
    enough once its author was demoted or removed. A document with no company
    stays its author's. Raises ForbiddenBillingDocumentError otherwise."""
    if access_repo is None or doc.company_id is None:
        if doc.user_id == user_id:
            return
        raise ForbiddenBillingDocumentError(doc.id)
    access = access_repo.find(user_id, doc.company_id)
    if access is not None and access.role == CompanyRole.ADMIN.value:
        return
    raise ForbiddenBillingDocumentError(doc.id)


def _assert_billing_template_access(template: BillingDocumentTemplate, user_id: UUID, access_repo=None) -> None:
    """Company templates are shared: a company-admin of the template's company may
    read and apply it, and so may its author while still attached to that company
    (the same rule as company billing documents). A template with no company stays
    private to its author."""
    _assert_billing_doc_access(template, user_id, access_repo)  # type: ignore[arg-type]


def _assert_billing_template_write_access(template: BillingDocumentTemplate, user_id: UUID, access_repo=None) -> None:
    """Editing or deleting a company template needs the company-admin role."""
    _assert_billing_doc_write_access(template, user_id, access_repo)  # type: ignore[arg-type]


def _linked_project(project_repo, doc: BillingDocument):
    """The project *doc* is linked to — printed as the document's "Objet" — or None.

    None too when the project no longer exists or no project_repo is wired (tests).
    """
    if doc.project_id is None or project_repo is None:
        return None
    return project_repo.find_by_id(doc.project_id)


def _conversion_link(doc_repo, doc: BillingDocument) -> tuple[Optional[UUID], Optional[str]]:
    """(id, status) of the facture created from *doc*, or (None, None).

    Always (None, None) for a facture and for a devis nobody converted — that
    absence is what lets a client stop offering the conversion a second time. The
    status tells a client whether that facture still locks the devis (it does
    until it is cancelled).
    """
    if doc.kind != BillingDocumentKind.DEVIS:
        return None, None
    facture = doc_repo.find_by_source_devis_id(doc.id)
    if facture is None:
        return None, None
    return facture.id, facture.status.value


def _assert_devis_not_locked(doc_repo, doc: BillingDocument) -> None:
    """A devis with a facture made from it stays as it is while that facture is live.

    Reverting it to sent, rejected or draft, or editing it, would leave a facture
    pointing at a devis that no longer says what was invoiced. Cancelling the
    facture releases the devis.
    """
    if doc.kind != BillingDocumentKind.DEVIS:
        return
    facture = doc_repo.find_by_source_devis_id(doc.id)
    if facture is not None and facture.status != BillingDocumentStatus.CANCELLED:
        raise DevisLockedByFactureError(doc.id)


def _snapshot_issuer_from_company(company: Company) -> dict:
    """Copy all issuer fields from a Company entity by value.

    Mirrors _snapshot_issuer but reads from the new Company domain entity
    (used by the post-phase-04 billing use-cases that accept company_id).
    Also derives effective_prefix from company.prefix_override (may be None).
    Returns a plain dict ready to be unpacked into BillingDocument kwargs.
    """
    return {
        "issuer_legal_name": company.legal_name,
        "issuer_address": company.address,
        "issuer_siret": company.siret,
        "issuer_tva_number": company.tva_number,
        "issuer_iban": company.iban,
        "issuer_bic": company.bic,
        "issuer_logo_url": company.logo_url,
    }


def _effective_prefix_from_company(company: Company) -> Optional[str]:
    """Return the effective document-number prefix for a company.

    Mirrors CompanyProfile.effective_prefix: returns prefix_override if set,
    otherwise None (the numbering module uses a default prefix in that case).
    """
    return company.prefix_override


def _compute_default_validity_until(issue_date: date) -> date:
    """Return default validity_until for a devis (issue_date + 30 days)."""
    return issue_date + timedelta(days=_DEFAULT_VALIDITY_DAYS)


def _compute_default_payment_due_date(issue_date: date) -> date:
    """Return default payment_due_date for a facture (issue_date + 30 days)."""
    return issue_date + timedelta(days=_DEFAULT_PAYMENT_DAYS)


def _items_from_inputs(item_inputs: list[ItemInput]) -> tuple[BillingDocumentItem, ...]:
    """Convert ItemInput DTOs to BillingDocumentItem value objects.

    Validates each item: description non-empty, quantity > 0, unit_price >= 0,
    vat_rate >= 0.  Raises ValueError on the first invalid item.
    """
    result: list[BillingDocumentItem] = []
    for raw in item_inputs:
        desc = raw.description.strip() if raw.description else ""
        if not desc:
            raise ValueError("Item description is required")
        qty = Decimal(str(raw.quantity))
        if qty <= 0:
            raise ValueError("Item quantity must be greater than 0")
        price = Decimal(str(raw.unit_price))
        if price < 0:
            raise ValueError("Item unit_price cannot be negative")
        vat = Decimal(str(raw.vat_rate))
        if vat < 0:
            raise ValueError("Item vat_rate cannot be negative")
        result.append(
            BillingDocumentItem(
                description=desc,
                quantity=qty,
                unit_price=price,
                vat_rate=vat,
                category=raw.category if hasattr(raw, "category") else None,
            )
        )
    return tuple(result)


def _build_doc_from_inputs(
    *,
    user_id: UUID,
    kind: BillingDocumentKind,
    document_number: str,
    issuer_snapshot: dict,
    recipient_name: str,
    items: tuple[BillingDocumentItem, ...],
    issue_date: Optional[date] = None,
    company_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    recipient_address: Optional[str] = None,
    recipient_email: Optional[str] = None,
    recipient_siret: Optional[str] = None,
    notes: Optional[str] = None,
    terms: Optional[str] = None,
    signature_block_text: Optional[str] = None,
    validity_until: Optional[date] = None,
    payment_due_date: Optional[date] = None,
    payment_terms: Optional[str] = None,
    source_devis_id: Optional[UUID] = None,
) -> BillingDocument:
    """Construct a new BillingDocument from validated inputs + issuer snapshot.

    Applies default dates for kind-specific fields when callers pass None:
      - validity_until    (devis)  → issue_date + 30 days
      - payment_due_date (facture) → issue_date + 30 days
    """
    now = datetime.now(timezone.utc)
    # Today on the business (Paris) calendar: the UTC day lags it after midnight.
    resolved_issue_date: date = issue_date if issue_date is not None else business_today(now)

    resolved_validity_until: Optional[date] = validity_until
    resolved_payment_due_date: Optional[date] = payment_due_date

    if kind == BillingDocumentKind.DEVIS and resolved_validity_until is None:
        resolved_validity_until = _compute_default_validity_until(resolved_issue_date)

    if kind == BillingDocumentKind.FACTURE and resolved_payment_due_date is None:
        resolved_payment_due_date = _compute_default_payment_due_date(resolved_issue_date)

    return BillingDocument(
        id=uuid4(),
        user_id=user_id,
        company_id=company_id,
        kind=kind,
        document_number=document_number,
        status=BillingDocumentStatus.DRAFT,
        issue_date=resolved_issue_date,
        created_at=now,
        updated_at=now,
        recipient_name=recipient_name,
        items=items,
        project_id=project_id,
        recipient_address=recipient_address,
        recipient_email=recipient_email,
        recipient_siret=recipient_siret,
        notes=notes,
        terms=terms,
        signature_block_text=signature_block_text,
        validity_until=resolved_validity_until,
        payment_due_date=resolved_payment_due_date,
        payment_terms=payment_terms,
        source_devis_id=source_devis_id,
        **issuer_snapshot,
    )
