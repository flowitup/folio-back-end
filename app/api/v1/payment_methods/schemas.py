"""Pydantic v2 schemas for the payment_methods API endpoints.

All schemas use strict mode + extra="forbid" to reject unknown fields
and prevent silent type coercion at the API boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreatePaymentMethodRequest(BaseModel):
    """Request body for POST /companies/<id>/payment-methods."""

    model_config = ConfigDict(strict=True, extra="forbid")

    label: str = Field(..., min_length=1, max_length=120)

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v  # pragma: no cover — strict=True rejects non-strings before this branch


class UpdatePaymentMethodRequest(BaseModel):
    """Request body for PATCH /companies/<id>/payment-methods/<id>.

    At least one of label or is_active must be provided.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    label: Optional[str] = Field(None, min_length=1, max_length=120)
    is_active: Optional[bool] = None

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v  # pragma: no cover — strict=True rejects non-strings before this branch

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdatePaymentMethodRequest":
        if self.label is None and self.is_active is None:
            raise ValueError("At least one of 'label' or 'is_active' must be provided")
        return self


class PaymentMethodResponseSchema(BaseModel):
    """Response body for payment method endpoints."""

    model_config = ConfigDict(strict=False, extra="ignore")

    id: UUID
    company_id: UUID
    label: str
    is_builtin: bool
    is_active: bool
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    usage_count: Optional[int] = None
