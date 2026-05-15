"""ListPaymentMethodsUseCase — returns payment methods for a company.

Access rules:
  - Global admin (*:*): can list for any company; however still returns 404
    if the company does not exist (no info leak even for admins).
  - Company member (non-admin): must have a user_company_access row for
    company_id else 404 (hide existence per red-team spec).
  - ``include_inactive=True`` is admin-only (soft-deleted rows for the
    management UI that shows the full history).
"""

from __future__ import annotations

from uuid import UUID

from app.application.payment_methods.dtos import PaymentMethodResponse
from app.application.payment_methods.ports import (
    IPaymentMethodRepository,
    IUserCompanyAccessRepository,
    RoleCheckerPort,
)

_ADMIN_PERMISSION = "*:*"


class ListPaymentMethodsUseCase:
    """Return payment methods for a company, optionally including inactive rows.

    ``usage_count`` is attached to every response item so the delete-confirm
    UX can surface how many historical invoices reference each method.

    Uses a single LEFT JOIN query (find_all_by_company_with_usage_count) to
    avoid the N+1 pattern of one COUNT query per method.
    """

    def __init__(
        self,
        payment_method_repo: IPaymentMethodRepository,
        role_checker: RoleCheckerPort,
        access_repo: IUserCompanyAccessRepository,
        company_repo: object,  # CompanyRepositoryPort — typed as object to avoid cross-context import
    ) -> None:
        self._repo = payment_method_repo
        self._role_checker = role_checker
        self._access_repo = access_repo
        self._company_repo = company_repo  # type: ignore[assignment]

    def execute(
        self,
        requester_id: UUID,
        company_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[PaymentMethodResponse]:
        from app.domain.payment_methods.exceptions import PaymentMethodNotFoundError

        is_admin = self._role_checker.has_permission(requester_id, _ADMIN_PERMISSION)

        # Guard 1: company must exist — even admins get 404 for non-existent companies
        # (no info leak: attacker cannot distinguish "company exists but no access"
        # from "company does not exist").
        company = self._company_repo.find_by_id(company_id)  # type: ignore[attr-defined]
        if company is None:
            raise PaymentMethodNotFoundError(company_id)

        # Guard 2: non-admin must have a user_company_access row for this company.
        if not is_admin:
            access = self._access_repo.find(requester_id, company_id)
            if access is None:
                raise PaymentMethodNotFoundError(company_id)

        # Inactive rows are admin-only; non-admins always get active-only list.
        effective_include_inactive = include_inactive and is_admin

        pairs = self._repo.find_all_by_company_with_usage_count(company_id, include_inactive=effective_include_inactive)

        return [PaymentMethodResponse.from_entity(m, usage_count=count) for m, count in pairs]
