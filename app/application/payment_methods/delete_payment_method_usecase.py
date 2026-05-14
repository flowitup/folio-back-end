"""DeletePaymentMethodUseCase — admin soft-deletes a payment method.

Soft-delete sets ``is_active = False``. The row is retained so that existing
invoice snapshot labels remain valid and auditable.

Builtin methods (Cash, company legal name) cannot be deleted; renaming is
still permitted via UpdatePaymentMethodUseCase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.payment_methods.ports import (
    IPaymentMethodRepository,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.payment_methods.exceptions import (
    BuiltinPaymentMethodDeletionError,
    PaymentMethodNotFoundError,
)

_ADMIN_PERMISSION = "*:*"


class DeletePaymentMethodUseCase:
    """Soft-delete a payment method (admin only).

    Raises:
        ForbiddenCompanyError: Caller does not hold admin permission.
        PaymentMethodNotFoundError: No method exists for the given ID.
        BuiltinPaymentMethodDeletionError: The method is builtin and cannot
            be deleted.
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
        requester_id: UUID,
        payment_method_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        from app.domain.companies.exceptions import ForbiddenCompanyError

        # 1. Admin guard
        is_admin = self._role_checker.has_permission(requester_id, _ADMIN_PERMISSION)
        if not is_admin:
            raise ForbiddenCompanyError(requester_id, payment_method_id)

        # 2. Load with lock to serialise concurrent soft-delete attempts
        method = self._repo.find_by_id_for_update(payment_method_id)
        if method is None:
            raise PaymentMethodNotFoundError(payment_method_id)

        # 3. Builtin guard — Cash + company-name cannot be removed
        if method.is_builtin:
            raise BuiltinPaymentMethodDeletionError(payment_method_id)

        # 4. Idempotent: already inactive → nothing to do
        if not method.is_active:
            return

        # 5. Apply soft-delete
        deactivated = method.with_updates(
            is_active=False,
            updated_at=datetime.now(timezone.utc),
        )
        with db_session.begin_nested():
            self._repo.save(deactivated)

        db_session.commit()
