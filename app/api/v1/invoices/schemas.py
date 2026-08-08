"""Invoice API request/response schemas."""

import re
from datetime import date
from typing import Literal, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.v1.projects.schemas import ErrorResponse  # reuse shared error schema

InvoiceTypeLiteral = Literal["released_funds", "labor", "materials_services", "others", "return"]
SettledViaLiteral = Literal["cash", "avoir"]


def normalize_invoice_type_value(value: object) -> object:
    """Map the pre-rename wire value 'refund' to its canonical 'return'.

    External clients (e.g. the folio MCP plugin) may still send 'refund';
    responses always emit 'return'.
    """
    return "return" if value == "refund" else value


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class InvoiceItemSchema(BaseModel):
    """A single line item on an invoice.

    unit_price carries no ge=0 bound — sign validation is type-dependent and
    enforced in the use-case (mixed-sign allowed for materials_services + return).
    quantity must be > 0; vat_rate is 0–100.
    """

    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit_price: float  # sign enforcement is in the use-case, not here
    vat_rate: float = Field(default=0.0, ge=0, le=100)


class CreateInvoiceSchema(BaseModel):
    """Request body for creating an invoice.

    refunds_invoice_id is optional; only valid when type='return'. When provided,
    the use-case validates that the target is a same-project materials_services invoice
    and enforces the cap (cumulative returns may not exceed the source total).
    Mixed-sign unit_price is allowed for materials_services and return types.
    service_month is optional; only valid when type='labor'. The use-case normalizes
    any day-of-month to day=1.
    settled_via/applied_to_invoice_id are optional; only valid when type='return'.
    applied_to_invoice_id additionally requires settled_via='avoir' — the use-case
    validates the target and enforces the applied-amount cap, then auto-aligns the
    return's payment_method_id to the target's.
    """

    type: InvoiceTypeLiteral

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    issue_date: date  # Pydantic parses ISO date string (YYYY-MM-DD) automatically
    recipient_name: str = Field(..., min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: List[InvoiceItemSchema] = Field(..., min_length=1)
    payment_method_id: Optional[UUID] = None
    tag_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[date] = None
    settled_via: Optional[SettledViaLiteral] = None
    applied_to_invoice_id: Optional[UUID] = None


class UpdateInvoiceSchema(BaseModel):
    """Request body for partially updating an invoice.

    payment_method_id, tag_id, refunds_invoice_id, service_month, settled_via, and
    applied_to_invoice_id use exclude_unset semantics:
      - field absent  → do not touch that field
      - field = null  → clear the field
      - field = value → set to that value
    Mixed-sign unit_price is allowed for materials_services and return types.
    """

    type: Optional[InvoiceTypeLiteral] = None
    issue_date: Optional[date] = None  # Pydantic parses ISO date string automatically

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[InvoiceItemSchema]] = None
    payment_method_id: Optional[UUID] = None
    tag_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[date] = None
    settled_via: Optional[SettledViaLiteral] = None
    applied_to_invoice_id: Optional[UUID] = None


_YYYY_MM = re.compile(r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$")


class ExportInvoicesQuery(BaseModel):
    """Pydantic v2 model for GET /invoices-export query params."""

    from_month: str = Field(alias="from")
    to_month: str = Field(alias="to")
    format: Literal["xlsx", "pdf"]
    type: Optional[InvoiceTypeLiteral] = None

    model_config = {"populate_by_name": True}

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type_alias(cls, v: object) -> object:
        return normalize_invoice_type_value(v)

    @field_validator("from_month", "to_month")
    @classmethod
    def _yyyy_mm(cls, v: str) -> str:
        if not _YYYY_MM.match(v):
            raise ValueError("must be YYYY-MM")
        return v

    @model_validator(mode="after")
    def _range(self):
        if self.from_month > self.to_month:
            raise ValueError("from must be <= to")
        fy, fm = int(self.from_month[:4]), int(self.from_month[5:7])
        ty, tm = int(self.to_month[:4]), int(self.to_month[5:7])
        span = (ty - fy) * 12 + (tm - fm) + 1
        if span > 24:
            raise ValueError("range may not exceed 24 months")
        return self


# Re-export for convenience
__all__ = [
    "InvoiceItemSchema",
    "CreateInvoiceSchema",
    "UpdateInvoiceSchema",
    "ExportInvoicesQuery",
    "ErrorResponse",
]
