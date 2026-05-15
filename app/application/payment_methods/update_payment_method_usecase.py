"""UpdatePaymentMethodUseCase — admin partially updates a payment method."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.payment_methods.dtos import PaymentMethodResponse, UpdatePaymentMethodInput
from app.application.payment_methods.ports import (
    IPaymentMethodRepository,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.payment_methods.exceptions import (
    BuiltinPaymentMethodDeletionError,
    PaymentMethodAlreadyExistsError,
    PaymentMethodNotFoundError,
)


def _validate_label(label: str) -> str:
    """Strip and validate label. Raises ValueError if blank after stripping."""
    stripped = label.strip()
    if not stripped:
        raise ValueError("Payment method label is required and cannot be blank")
    if len(stripped) > 120:
        raise ValueError("Payment method label must be 120 characters or fewer")
    return stripped


_ADMIN_PERMISSION = "*:*"


class UpdatePaymentMethodUseCase:
    """Partially update a payment method (admin only).

    ``label`` and ``is_active`` may be supplied independently or together.
    Only supplied (non-None) fields are applied; all others carry over.

    Raises:
        ForbiddenCompanyError: Caller does not hold admin permission.
        PaymentMethodNotFoundError: No method exists for the given ID.
        PaymentMethodAlreadyExistsError: The new label collides (case-insensitive)
            with an existing active method in the same company.
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
        inp: UpdatePaymentMethodInput,
        db_session: TransactionalSessionPort,
    ) -> PaymentMethodResponse:
        from app.domain.companies.exceptions import ForbiddenCompanyError

        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.requester_id, _ADMIN_PERMISSION)
        if not is_admin:
            raise ForbiddenCompanyError(inp.requester_id, inp.payment_method_id)

        # 2. Load entity with lock (low-contention admin write, but keeps snapshot safe)
        method = self._repo.find_by_id_for_update(inp.payment_method_id)
        if method is None:
            raise PaymentMethodNotFoundError(inp.payment_method_id)

        # 2b. Cross-tenant guard: URL company_id must match method's company_id.
        # Return 404 (not 403) to avoid leaking that the method exists in another company.
        if method.company_id != inp.company_id:
            raise PaymentMethodNotFoundError(inp.payment_method_id)

        # 3. Validate + deduplicate label if supplied
        new_label = None
        if inp.label is not None:
            new_label = _validate_label(inp.label)
            if new_label.lower() != method.label.lower():
                # Only check collision when the normalised label actually changes
                existing = self._repo.find_by_label_ci(method.company_id, new_label, only_active=True)
                if existing is not None and existing.id != method.id:
                    raise PaymentMethodAlreadyExistsError(method.company_id, new_label)

        # 3b. Builtin guard — cannot deactivate builtin methods (rename is still allowed)
        if inp.is_active is False and method.is_builtin:
            raise BuiltinPaymentMethodDeletionError(method.id)

        # 4. Apply updates
        updated = method.with_updates(
            label=new_label,
            is_active=inp.is_active,
            updated_at=datetime.now(timezone.utc),
        )

        with db_session.begin_nested():
            saved = self._repo.save(updated)

        db_session.commit()
        return PaymentMethodResponse.from_entity(saved)
