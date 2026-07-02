"""Invoice API request/response schemas."""

import re
from datetime import date
from typing import Literal, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.v1.projects.schemas import ErrorResponse  # reuse shared error schema

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class InvoiceItemSchema(BaseModel):
    """A single line item on an invoice.

    unit_price carries no ge=0 bound — sign validation is type-dependent and
    enforced in the use-case (mixed-sign allowed for materials_services + refund).
    quantity must be > 0; vat_rate is 0–100.
    """

    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit_price: float  # sign enforcement is in the use-case, not here
    vat_rate: float = Field(default=0.0, ge=0, le=100)


class CreateInvoiceSchema(BaseModel):
    """Request body for creating an invoice.

    refunds_invoice_id is optional; only valid when type='refund'. When provided,
    the use-case validates that the target is a same-project materials_services invoice
    and enforces the cap (cumulative refunds may not exceed the source total).
    Mixed-sign unit_price is allowed for materials_services and refund types.
    service_month is optional; only valid when type='labor'. The use-case normalizes
    any day-of-month to day=1.
    """

    type: Literal["released_funds", "labor", "materials_services", "others", "refund"]
    issue_date: date  # Pydantic parses ISO date string (YYYY-MM-DD) automatically
    recipient_name: str = Field(..., min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: List[InvoiceItemSchema] = Field(..., min_length=1)
    payment_method_id: Optional[UUID] = None
    tag_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[date] = None


class UpdateInvoiceSchema(BaseModel):
    """Request body for partially updating an invoice.

    payment_method_id, tag_id, refunds_invoice_id, and service_month use
    exclude_unset semantics:
      - field absent  → do not touch that field
      - field = null  → clear the field
      - field = value → set to that value
    Mixed-sign unit_price is allowed for materials_services and refund types.
    """

    type: Optional[Literal["released_funds", "labor", "materials_services", "others", "refund"]] = None
    issue_date: Optional[date] = None  # Pydantic parses ISO date string automatically
    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[InvoiceItemSchema]] = None
    payment_method_id: Optional[UUID] = None
    tag_id: Optional[UUID] = None
    refunds_invoice_id: Optional[UUID] = None
    service_month: Optional[date] = None


_YYYY_MM = re.compile(r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$")


class ExportInvoicesQuery(BaseModel):
    """Pydantic v2 model for GET /invoices-export query params."""

    from_month: str = Field(alias="from")
    to_month: str = Field(alias="to")
    format: Literal["xlsx", "pdf"]
    type: Optional[Literal["released_funds", "labor", "materials_services", "others", "refund"]] = None

    model_config = {"populate_by_name": True}

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
