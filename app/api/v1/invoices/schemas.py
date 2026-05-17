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
    """A single line item on an invoice."""

    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit_price: float = Field(..., ge=0)


class CreateInvoiceSchema(BaseModel):
    """Request body for creating an invoice."""

    type: Literal["released_funds", "labor", "materials_services"]
    issue_date: date  # Pydantic parses ISO date string (YYYY-MM-DD) automatically
    recipient_name: str = Field(..., min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: List[InvoiceItemSchema] = Field(..., min_length=1)
    payment_method_id: Optional[UUID] = None


class UpdateInvoiceSchema(BaseModel):
    """Request body for partially updating an invoice (type is immutable).

    payment_method_id uses exclude_unset semantics to distinguish:
      - field absent  → do not touch payment method
      - field = null  → clear payment method
      - field = UUID  → set payment method
    """

    issue_date: Optional[date] = None  # Pydantic parses ISO date string automatically
    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[InvoiceItemSchema]] = None
    payment_method_id: Optional[UUID] = None


_YYYY_MM = re.compile(r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$")


class ExportInvoicesQuery(BaseModel):
    """Pydantic v2 model for GET /invoices-export query params."""

    from_month: str = Field(alias="from")
    to_month: str = Field(alias="to")
    format: Literal["xlsx", "pdf"]
    type: Optional[Literal["released_funds", "labor", "materials_services"]] = None

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
