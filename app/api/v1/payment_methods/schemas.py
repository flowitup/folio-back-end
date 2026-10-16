"""Pydantic v2 schemas for the payment_methods API endpoints.

All schemas use strict mode + extra="forbid" to reject unknown fields
and prevent silent type coercion at the API boundary.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreatePaymentMethodRequest(BaseModel):
    """Request body for POST /companies/<id>/payment-methods."""

    model_config = ConfigDict(strict=True, extra="forbid")

    label: str = Field(..., min_length=1, max_length=120)
    # The purse the method's expenses count in, chosen at creation (both false = no purse).
    # A method created with a label only always landed in no purse, so its expenses could
    # never be reimbursed until someone found the flags behind "rename".
    is_company_payment: bool = False
    is_personal_payment: bool = False

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v  # pragma: no cover — strict=True rejects non-strings before this branch

    @model_validator(mode="after")
    def one_purse_at_most(self) -> "CreatePaymentMethodRequest":
        if self.is_company_payment and self.is_personal_payment:
            raise ValueError("A payment method cannot be both a company and a personal payment")
        return self


class UpdatePaymentMethodRequest(BaseModel):
    """Request body for PATCH /companies/<id>/payment-methods/<id>.

    At least one of label, is_active, is_company_payment, or is_personal_payment
    must be provided.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    label: Optional[str] = Field(None, min_length=1, max_length=120)
    is_active: Optional[bool] = None
    # Toggle whether invoices paid via this method count toward "spent by company".
    # Applies to any method (including builtins); admin-only, same authz as other edits.
    is_company_payment: Optional[bool] = None
    # Toggle whether invoices paid via this method count toward "spent personally".
    # Mutually exclusive with is_company_payment — the use-case validates the
    # EFFECTIVE final state (existing value merged with this update).
    is_personal_payment: Optional[bool] = None

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v  # pragma: no cover — strict=True rejects non-strings before this branch

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdatePaymentMethodRequest":
        if (
            self.label is None
            and self.is_active is None
            and self.is_company_payment is None
            and self.is_personal_payment is None
        ):
            raise ValueError(
                "At least one of 'label', 'is_active', 'is_company_payment', "
                "or 'is_personal_payment' must be provided"
            )
        return self
