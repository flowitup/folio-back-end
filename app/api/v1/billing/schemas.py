"""Pydantic v2 request/response schemas for the billing API.

Strict mode (extra='forbid') enforced on all schemas to reject unknown fields.
Decimal is used for all monetary / rate values to avoid float precision issues.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared sub-schema
# ---------------------------------------------------------------------------


class ItemSchema(_StrictBase):
    """A single line item used in create / update / template schemas."""

    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=Decimal("0"), le=Decimal("9999999"))
    unit_price: Decimal = Field(..., ge=Decimal("0"), le=Decimal("999999999"))
    vat_rate: Decimal = Field(..., ge=Decimal("0"), le=Decimal("100"))


# ---------------------------------------------------------------------------
# Billing document request schemas
# ---------------------------------------------------------------------------


class CreateBillingDocumentRequest(_StrictBase):
    """Request body for POST /billing-documents."""

    kind: Literal["devis", "facture"]
    recipient_name: str = Field(..., min_length=1, max_length=255)
    items: list[ItemSchema] = Field(..., min_length=1, max_length=200)
    company_id: UUID  # required — legacy CompanyProfile fallback removed in phase 05
    project_id: Optional[UUID] = None
    recipient_address: Optional[str] = Field(None, max_length=500)
    recipient_email: Optional[EmailStr] = None
    recipient_siret: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    terms: Optional[str] = Field(None, max_length=2000)
    signature_block_text: Optional[str] = Field(None, max_length=500)
    validity_until: Optional[date] = None
    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = Field(None, max_length=500)
    issue_date: Optional[date] = None


class UpdateBillingDocumentRequest(_StrictBase):
    """Request body for PUT /billing-documents/<id>.

    Fields immutable after creation are intentionally absent:
    kind, document_number, status, source_devis_id, and all issuer_* fields.
    Any attempt to send them will be rejected by extra='forbid'.
    """

    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    items: Optional[list[ItemSchema]] = Field(None, max_length=200)
    project_id: Optional[UUID] = None
    recipient_address: Optional[str] = Field(None, max_length=500)
    recipient_email: Optional[EmailStr] = None
    recipient_siret: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    terms: Optional[str] = Field(None, max_length=2000)
    signature_block_text: Optional[str] = Field(None, max_length=500)
    validity_until: Optional[date] = None
    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = Field(None, max_length=500)
    issue_date: Optional[date] = None


class UpdateStatusRequest(_StrictBase):
    """Request body for PATCH /billing-documents/<id>/status."""

    new_status: Literal["draft", "sent", "accepted", "rejected", "expired", "paid", "overdue", "cancelled"]


class CloneRequest(_StrictBase):
    """Optional body for POST /billing-documents/<id>/clone."""

    override_kind: Optional[Literal["devis", "facture"]] = None
    company_id: Optional[UUID] = None  # None → inherit from source document


class ConvertRequest(_StrictBase):
    """Optional body for POST /billing-documents/<id>/convert-to-facture.

    Both fields are optional — empty body {} is accepted.
    """

    payment_due_date: Optional[date] = None
    payment_terms: Optional[str] = None
    company_id: Optional[UUID] = None  # None → inherit from source document


class ApplyTemplateRequest(_StrictBase):
    """Body for POST /billing-documents/from-template/<template_id>."""

    recipient_name: str = Field(..., min_length=1, max_length=255)
    company_id: Optional[UUID] = None  # None → resolved to caller's primary company
    recipient_address: Optional[str] = None
    recipient_email: Optional[EmailStr] = None
    recipient_siret: Optional[str] = None
    project_id: Optional[UUID] = None
    issue_date: Optional[date] = None


# ---------------------------------------------------------------------------
# Template request schemas
# ---------------------------------------------------------------------------


class CreateTemplateRequest(_StrictBase):
    """Request body for POST /billing-document-templates."""

    kind: Literal["devis", "facture"]
    name: str = Field(..., min_length=1, max_length=120)
    items: list[ItemSchema] = Field(default_factory=list)
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = Field(None, ge=Decimal("0"), le=Decimal("100"))


class UpdateTemplateRequest(_StrictBase):
    """Request body for PUT /billing-document-templates/<id>."""

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    items: Optional[list[ItemSchema]] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = Field(None, ge=Decimal("0"), le=Decimal("100"))


# ---------------------------------------------------------------------------
# Company profile request schema
# ---------------------------------------------------------------------------


class UpsertCompanyProfileRequest(_StrictBase):
    """Request body for PUT /company-profile."""

    legal_name: str = Field(..., min_length=1, max_length=255)
    address: str = Field(..., min_length=1)
    siret: Optional[str] = None
    tva_number: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[HttpUrl] = None
    default_payment_terms: Optional[str] = Field(None, max_length=500)
    prefix_override: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,8}$", max_length=8)
