"""Invoice API request/response schemas."""

from datetime import date
from typing import Literal, List, Optional

from pydantic import BaseModel, Field

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

    type: Literal["client", "labor", "supplier"]
    issue_date: date  # Pydantic parses ISO date string (YYYY-MM-DD) automatically
    recipient_name: str = Field(..., min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: List[InvoiceItemSchema] = Field(..., min_length=1)


class UpdateInvoiceSchema(BaseModel):
    """Request body for partially updating an invoice (type is immutable)."""

    issue_date: Optional[date] = None  # Pydantic parses ISO date string automatically
    recipient_name: Optional[str] = Field(None, min_length=1, max_length=255)
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[InvoiceItemSchema]] = None


# Re-export for convenience
__all__ = [
    "InvoiceItemSchema",
    "CreateInvoiceSchema",
    "UpdateInvoiceSchema",
    "ErrorResponse",
]
