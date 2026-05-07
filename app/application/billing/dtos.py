"""DTOs (frozen dataclasses) for the billing application layer.

Input DTOs: carry caller-supplied data into use-cases.
Response DTOs: carry serialisation-friendly data out of use-cases.

No Pydantic here — Pydantic is the API boundary concern (phase 04).
Decimal stays as Decimal throughout; callers quantize at their boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.template import BillingDocumentTemplate

# ---------------------------------------------------------------------------
# Shared sub-input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemInput:
    """A single line item supplied by the caller."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal  # percent, e.g. Decimal("20")


# ---------------------------------------------------------------------------
# Document input DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateBillingDocumentInput:
    """Input for CreateBillingDocumentUseCase."""

    user_id: UUID
    kind: BillingDocumentKind
    recipient_name: str
    items: list[ItemInput]
    company_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    recipient_address: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_siret: Optional[str] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    signature_block_text: Optional[str] = None
    validity_until: Optional[date] = None  # devis; defaults to issue_date+30
    payment_due_date: Optional[date] = None  # facture; defaults to issue_date+30
    payment_terms: Optional[str] = None  # facture; defaults to company profile
    issue_date: Optional[date] = None  # defaults to today


@dataclass(frozen=True)
class UpdateBillingDocumentInput:
    """Input for UpdateBillingDocumentUseCase — all fields optional except id."""

    id: UUID
    user_id: UUID
    recipient_name: Optional[str] = None
    recipient_address: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_siret: Optional[str] = None
    items: Optional[list[ItemInput]] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    signature_block_text: Optional[str] = None
    validity_until: Optional[date] = None
    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = None
    project_id: Optional[UUID] = None
    issue_date: Optional[date] = None


@dataclass(frozen=True)
class CloneBillingDocumentInput:
    """Input for CloneBillingDocumentUseCase."""

    source_id: UUID
    user_id: UUID
    override_kind: Optional[BillingDocumentKind] = None  # None = same kind as source
    company_id: Optional[UUID] = None  # None → use source doc's company_id


@dataclass(frozen=True)
class ConvertDevisToFactureInput:
    """Input for ConvertDevisToFactureUseCase."""

    source_devis_id: UUID
    user_id: UUID
    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = None
    company_id: Optional[UUID] = None  # None → use source doc's company_id


@dataclass(frozen=True)
class UpdateStatusInput:
    """Input for UpdateBillingDocumentStatusUseCase."""

    id: UUID
    user_id: UUID
    new_status: BillingDocumentStatus


# ---------------------------------------------------------------------------
# Template input DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateTemplateInput:
    """Input for CreateTemplateUseCase."""

    user_id: UUID
    kind: BillingDocumentKind
    name: str
    items: list[ItemInput] = field(default_factory=list)
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = None


@dataclass(frozen=True)
class UpdateTemplateInput:
    """Input for UpdateTemplateUseCase — all fields optional except id."""

    id: UUID
    user_id: UUID
    name: Optional[str] = None
    items: Optional[list[ItemInput]] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = None


@dataclass(frozen=True)
class ApplyTemplateInput:
    """Input for ApplyTemplateToCreateDocumentUseCase.

    Everything not listed here is copied from the template's stored content.
    """

    template_id: UUID
    user_id: UUID
    recipient_name: str
    company_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    recipient_address: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_siret: Optional[str] = None
    issue_date: Optional[date] = None  # defaults to today


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemResponse:
    """Serialisable line item with computed totals."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    total_ht: Decimal
    total_tva: Decimal
    total_ttc: Decimal


@dataclass(frozen=True)
class BillingDocumentResponse:
    """Serialisable billing document with computed totals."""

    id: UUID
    user_id: UUID
    kind: str
    document_number: str
    status: str
    issue_date: date
    created_at: datetime
    updated_at: datetime
    recipient_name: str
    issuer_legal_name: str
    issuer_address: str
    items: list[ItemResponse]
    total_ht: Decimal
    total_tva: Decimal
    total_ttc: Decimal
    company_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    validity_until: Optional[date] = None
    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = None
    recipient_address: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_siret: Optional[str] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    signature_block_text: Optional[str] = None
    issuer_siret: Optional[str] = None
    issuer_tva_number: Optional[str] = None
    issuer_iban: Optional[str] = None
    issuer_bic: Optional[str] = None
    issuer_logo_url: Optional[str] = None
    source_devis_id: Optional[UUID] = None

    @staticmethod
    def from_entity(doc: BillingDocument) -> "BillingDocumentResponse":
        """Build response DTO from a domain entity."""
        item_responses = [
            ItemResponse(
                description=it.description,
                quantity=it.quantity,
                unit_price=it.unit_price,
                vat_rate=it.vat_rate,
                total_ht=it.total_ht,
                total_tva=it.total_tva,
                total_ttc=it.total_ttc,
            )
            for it in doc.items
        ]
        return BillingDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            company_id=doc.company_id,
            kind=doc.kind.value,
            document_number=doc.document_number,
            status=doc.status.value,
            issue_date=doc.issue_date,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            recipient_name=doc.recipient_name,
            issuer_legal_name=doc.issuer_legal_name,
            issuer_address=doc.issuer_address,
            items=item_responses,
            total_ht=doc.total_ht,
            total_tva=doc.total_tva,
            total_ttc=doc.total_ttc,
            project_id=doc.project_id,
            validity_until=doc.validity_until,
            payment_due_date=doc.payment_due_date,
            payment_terms=doc.payment_terms,
            recipient_address=doc.recipient_address,
            recipient_email=doc.recipient_email,
            recipient_siret=doc.recipient_siret,
            notes=doc.notes,
            terms=doc.terms,
            signature_block_text=doc.signature_block_text,
            issuer_siret=doc.issuer_siret,
            issuer_tva_number=doc.issuer_tva_number,
            issuer_iban=doc.issuer_iban,
            issuer_bic=doc.issuer_bic,
            issuer_logo_url=doc.issuer_logo_url,
            source_devis_id=doc.source_devis_id,
        )


@dataclass(frozen=True)
class BillingTemplateResponse:
    """Serialisable billing template."""

    id: UUID
    user_id: UUID
    kind: str
    name: str
    created_at: datetime
    updated_at: datetime
    items: list[ItemResponse]
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = None

    @staticmethod
    def from_entity(tpl: BillingDocumentTemplate) -> "BillingTemplateResponse":
        """Build response DTO from a domain entity."""
        item_responses = [
            ItemResponse(
                description=it.description,
                quantity=it.quantity,
                unit_price=it.unit_price,
                vat_rate=it.vat_rate,
                total_ht=it.total_ht,
                total_tva=it.total_tva,
                total_ttc=it.total_ttc,
            )
            for it in tpl.items
        ]
        return BillingTemplateResponse(
            id=tpl.id,
            user_id=tpl.user_id,
            kind=tpl.kind.value,
            name=tpl.name,
            created_at=tpl.created_at,
            updated_at=tpl.updated_at,
            items=item_responses,
            notes=tpl.notes,
            terms=tpl.terms,
            default_vat_rate=tpl.default_vat_rate,
        )
