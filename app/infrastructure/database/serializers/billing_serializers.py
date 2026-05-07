"""Domain ↔ ORM serialization helpers for the billing bounded context.

Decimal precision: stored as string inside JSONB so round-trips are exact.
All serialize_* functions write to an existing ORM model instance (mutate in-place).
All deserialize_* functions return fresh domain entities.
"""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.billing.company_profile import CompanyProfile
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.value_objects import BillingDocumentItem

if TYPE_CHECKING:
    from app.infrastructure.database.models.billing_document import BillingDocumentModel
    from app.infrastructure.database.models.billing_document_template import (
        BillingDocumentTemplateModel,
    )
    from app.infrastructure.database.models.company_profile import CompanyProfileModel


# ---------------------------------------------------------------------------
# Item helpers
# ---------------------------------------------------------------------------


def serialize_item(item: BillingDocumentItem) -> dict:
    """Convert a BillingDocumentItem value object to a JSON-serialisable dict.

    Decimal values are stored as strings to preserve full precision across
    the JSONB round-trip (avoiding float representation errors).
    """
    return {
        "description": item.description,
        "quantity": str(item.quantity),
        "unit_price": str(item.unit_price),
        "vat_rate": str(item.vat_rate),
    }


def deserialize_item(d: dict) -> BillingDocumentItem:
    """Reconstruct a BillingDocumentItem from a stored dict.

    Decimal fields are parsed from their string representation to restore
    exact precision.
    """
    return BillingDocumentItem(
        description=d["description"],
        quantity=Decimal(d["quantity"]),
        unit_price=Decimal(d["unit_price"]),
        vat_rate=Decimal(d["vat_rate"]),
    )


# ---------------------------------------------------------------------------
# BillingDocument
# ---------------------------------------------------------------------------


def serialize_doc_to_orm(doc: BillingDocument, model: "BillingDocumentModel") -> None:
    """Write all domain fields from *doc* into *model* (in-place mutation).

    Caller is responsible for session.add() and flush/commit.
    """
    model.id = doc.id
    model.user_id = doc.user_id
    model.company_id = doc.company_id
    model.project_id = doc.project_id
    model.kind = doc.kind.value
    model.document_number = doc.document_number
    model.status = doc.status.value
    model.issue_date = doc.issue_date
    model.validity_until = doc.validity_until
    model.payment_due_date = doc.payment_due_date
    model.payment_terms = doc.payment_terms
    model.recipient_name = doc.recipient_name
    model.recipient_address = doc.recipient_address
    model.recipient_email = doc.recipient_email
    model.recipient_siret = doc.recipient_siret
    model.notes = doc.notes
    model.terms = doc.terms
    model.signature_block_text = doc.signature_block_text
    model.items = [serialize_item(i) for i in doc.items]
    model.issuer_legal_name = doc.issuer_legal_name
    model.issuer_address = doc.issuer_address
    model.issuer_siret = doc.issuer_siret
    model.issuer_tva_number = doc.issuer_tva_number
    model.issuer_iban = doc.issuer_iban
    model.issuer_bic = doc.issuer_bic
    model.issuer_logo_url = doc.issuer_logo_url
    model.source_devis_id = doc.source_devis_id
    model.created_at = doc.created_at
    model.updated_at = doc.updated_at


def deserialize_orm_to_doc(model: "BillingDocumentModel") -> BillingDocument:
    """Reconstruct a BillingDocument domain entity from an ORM model row."""
    items = tuple(deserialize_item(d) for d in (model.items or []))

    created_at = model.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    updated_at = model.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    return BillingDocument(
        id=UUID(str(model.id)),
        user_id=UUID(str(model.user_id)),
        company_id=UUID(str(model.company_id)) if model.company_id else None,
        project_id=UUID(str(model.project_id)) if model.project_id else None,
        kind=BillingDocumentKind(model.kind),
        document_number=model.document_number,
        status=BillingDocumentStatus(model.status),
        issue_date=model.issue_date,
        validity_until=model.validity_until,
        payment_due_date=model.payment_due_date,
        payment_terms=model.payment_terms,
        recipient_name=model.recipient_name,
        recipient_address=model.recipient_address,
        recipient_email=model.recipient_email,
        recipient_siret=model.recipient_siret,
        notes=model.notes,
        terms=model.terms,
        signature_block_text=model.signature_block_text,
        items=items,
        issuer_legal_name=model.issuer_legal_name,
        issuer_address=model.issuer_address,
        issuer_siret=model.issuer_siret,
        issuer_tva_number=model.issuer_tva_number,
        issuer_iban=model.issuer_iban,
        issuer_bic=model.issuer_bic,
        issuer_logo_url=model.issuer_logo_url,
        source_devis_id=UUID(str(model.source_devis_id)) if model.source_devis_id else None,
        created_at=created_at,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# BillingDocumentTemplate
# ---------------------------------------------------------------------------


def serialize_template_to_orm(
    template: BillingDocumentTemplate,
    model: "BillingDocumentTemplateModel",
) -> None:
    """Write all domain fields from *template* into *model* (in-place mutation)."""
    model.id = template.id
    model.user_id = template.user_id
    model.kind = template.kind.value
    model.name = template.name
    model.notes = template.notes
    model.terms = template.terms
    model.default_vat_rate = template.default_vat_rate  # Numeric(5,2); None allowed
    model.items = [serialize_item(i) for i in template.items]
    model.created_at = template.created_at
    model.updated_at = template.updated_at


def deserialize_orm_to_template(
    model: "BillingDocumentTemplateModel",
) -> BillingDocumentTemplate:
    """Reconstruct a BillingDocumentTemplate domain entity from an ORM model row."""
    items = tuple(deserialize_item(d) for d in (model.items or []))

    created_at = model.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    updated_at = model.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    default_vat_rate = Decimal(str(model.default_vat_rate)) if model.default_vat_rate is not None else None

    return BillingDocumentTemplate(
        id=UUID(str(model.id)),
        user_id=UUID(str(model.user_id)),
        kind=BillingDocumentKind(model.kind),
        name=model.name,
        notes=model.notes,
        terms=model.terms,
        default_vat_rate=default_vat_rate,
        items=items,
        created_at=created_at,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# CompanyProfile
# ---------------------------------------------------------------------------


def serialize_profile_to_orm(
    profile: CompanyProfile,
    model: "CompanyProfileModel",
) -> None:
    """Write all domain fields from *profile* into *model* (in-place mutation)."""
    model.user_id = profile.user_id
    model.legal_name = profile.legal_name
    model.address = profile.address
    model.siret = profile.siret
    model.tva_number = profile.tva_number
    model.iban = profile.iban
    model.bic = profile.bic
    model.logo_url = profile.logo_url
    model.default_payment_terms = profile.default_payment_terms
    model.prefix_override = profile.prefix_override
    model.created_at = profile.created_at
    model.updated_at = profile.updated_at


def deserialize_orm_to_profile(model: "CompanyProfileModel") -> CompanyProfile:
    """Reconstruct a CompanyProfile domain entity from an ORM model row."""
    created_at = model.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    updated_at = model.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    return CompanyProfile(
        user_id=UUID(str(model.user_id)),
        legal_name=model.legal_name,
        address=model.address,
        siret=model.siret,
        tva_number=model.tva_number,
        iban=model.iban,
        bic=model.bic,
        logo_url=model.logo_url,
        default_payment_terms=model.default_payment_terms,
        prefix_override=model.prefix_override,
        created_at=created_at,
        updated_at=updated_at,
    )
