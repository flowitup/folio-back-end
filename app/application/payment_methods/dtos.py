"""DTOs (frozen dataclasses) for the payment_methods application layer.

Input DTOs: carry caller-supplied data into use-cases.
Response DTOs: carry serialisation-friendly data out of use-cases.

No Pydantic here — Pydantic is the API boundary concern (phase 03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.domain.payment_methods.payment_method import PaymentMethod


# ---------------------------------------------------------------------------
# Input DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatePaymentMethodInput:
    """Input for CreatePaymentMethodUseCase."""

    requester_id: UUID
    company_id: UUID
    label: str


@dataclass(frozen=True)
class UpdatePaymentMethodInput:
    """Input for UpdatePaymentMethodUseCase — all mutation fields optional."""

    requester_id: UUID
    payment_method_id: UUID
    label: Optional[str] = None
    is_active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Response DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentMethodResponse:
    """Serialisable payment method.

    ``usage_count`` is populated only when explicitly requested (e.g. via the
    list endpoint for the delete-confirm UX). It is None by default to avoid
    the extra ``COUNT`` query on every read path.
    """

    id: UUID
    company_id: UUID
    label: str
    is_builtin: bool
    is_active: bool
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    usage_count: Optional[int] = None

    @staticmethod
    def from_entity(
        method: PaymentMethod,
        *,
        usage_count: Optional[int] = None,
    ) -> "PaymentMethodResponse":
        """Build response DTO from a domain entity.

        Args:
            method: The domain entity to serialise.
            usage_count: Optional count of invoices referencing this method.
                         Pass None (default) to omit from the response.
        """
        return PaymentMethodResponse(
            id=method.id,
            company_id=method.company_id,
            label=method.label,
            is_builtin=method.is_builtin,
            is_active=method.is_active,
            created_by=method.created_by,
            created_at=method.created_at,
            updated_at=method.updated_at,
            usage_count=usage_count,
        )
