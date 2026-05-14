"""ListPaymentMethodsUseCase — returns payment methods for a company.

Access rules:
  - Any company member may list active methods (read path).
  - ``include_inactive=True`` is admin-only (soft-deleted rows for the
    management UI that shows the full history).
"""

from __future__ import annotations

from uuid import UUID

from app.application.payment_methods.dtos import PaymentMethodResponse
from app.application.payment_methods.ports import IPaymentMethodRepository, RoleCheckerPort

_ADMIN_PERMISSION = "*:*"


class ListPaymentMethodsUseCase:
    """Return payment methods for a company, optionally including inactive rows.

    ``usage_count`` is attached to every response item so the delete-confirm
    UX can surface how many historical invoices reference each method.
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
        company_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[PaymentMethodResponse]:
        # Inactive rows are admin-only; non-admins always get active-only list.
        is_admin = self._role_checker.has_permission(requester_id, _ADMIN_PERMISSION)
        effective_include_inactive = include_inactive and is_admin

        methods = self._repo.find_all_by_company(company_id, include_inactive=effective_include_inactive)

        return [
            PaymentMethodResponse.from_entity(
                m,
                usage_count=self._repo.count_invoices_referencing(m.id),
            )
            for m in methods
        ]
