"""CreatePaymentMethodUseCase — admin creates a new payment method for a company."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.application.payment_methods.dtos import CreatePaymentMethodInput, PaymentMethodResponse
from app.application.payment_methods.ports import (
    IPaymentMethodRepository,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.payment_methods.exceptions import (
    PaymentMethodAlreadyExistsError,
)
from app.domain.payment_methods.payment_method import PaymentMethod

_ADMIN_PERMISSION = "*:*"


def _validate_label(label: str) -> str:
    """Strip and validate label. Raises ValueError if blank after stripping."""
    stripped = label.strip()
    if not stripped:
        raise ValueError("Payment method label is required and cannot be blank")
    if len(stripped) > 120:
        raise ValueError("Payment method label must be 120 characters or fewer")
    return stripped


class CreatePaymentMethodUseCase:
    """Create a new active payment method for a company (admin only).

    Raises:
        ForbiddenCompanyError: Caller does not hold admin permission.
        PaymentMethodAlreadyExistsError: A case-insensitive label collision
            exists among active rows for the same company.
        ValueError: Label is blank or exceeds 120 characters.
    """

    def __init__(
        self,
        payment_method_repo: IPaymentMethodRepository,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._repo = payment_method_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: CreatePaymentMethodInput,
        db_session: TransactionalSessionPort,
    ) -> PaymentMethodResponse:
        from app.domain.companies.exceptions import ForbiddenCompanyError

        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.requester_id, _ADMIN_PERMISSION)
        if not is_admin:
            raise ForbiddenCompanyError(inp.requester_id, inp.company_id)

        # 2. Validate label
        label = _validate_label(inp.label)

        # 3. Duplicate-label guard (case-insensitive, active rows only)
        existing = self._repo.find_by_label_ci(inp.company_id, label, only_active=True)
        if existing is not None:
            raise PaymentMethodAlreadyExistsError(inp.company_id, label)

        # 4. Build and persist entity
        now = datetime.now(timezone.utc)
        method = PaymentMethod(
            id=uuid4(),
            company_id=inp.company_id,
            label=label,
            is_builtin=False,
            is_active=True,
            created_by=inp.requester_id,
            created_at=now,
            updated_at=now,
        )

        with db_session.begin_nested():
            saved = self._repo.save(method)

        db_session.commit()
        return PaymentMethodResponse.from_entity(saved)
