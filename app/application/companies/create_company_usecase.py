"""CreateCompanyUseCase — self-service: any authenticated user creates a new company."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from app.application.companies._helpers import (
    _validate_address,
    _validate_legal_name,
    _validate_prefix_override,
)
from app.application.companies.dtos import CompanyResponse, CreateCompanyInput
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.company import Company
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess

if TYPE_CHECKING:
    from app.application.payment_methods.seed_payment_methods_for_company_usecase import (
        SeedPaymentMethodsForCompanyUseCase,
    )
    from app.application.labor.seed_default_labor_roles import SeedDefaultLaborRolesUseCase

_log = logging.getLogger(__name__)


class CreateCompanyUseCase:
    """Create a new company — self-service (Phase 2 D1/goal 1): any authenticated
    user may create a company, no platform ``*:*`` permission required.

    Pre-conditions:
      - legal_name and address are required non-blank strings.
      - prefix_override, when supplied, must match ^[A-Z0-9]{1,8}$.

    Post-creation (same transaction as the company insert):
      - The caller is attached as the company's ``admin`` (D6: no more owner
        bypass — the creator is a real company admin from day one).
      - ``is_primary=True`` when this is the caller's first company.

    Post-commit:
      - If ``seed_payment_methods`` is provided, the use-case seeds the
        builtin payment methods (Cash + legal_name) for the new company.
      - If ``seed_default_labor_roles`` is provided, the use-case seeds the
        two default labor roles ("Thợ chính" / "Thợ phụ") for the new company.
      - Both seed failures are logged and swallowed — they must NOT roll
        back the company creation.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
        access_repo: Optional[UserCompanyAccessRepositoryPort] = None,
        seed_payment_methods: Optional["SeedPaymentMethodsForCompanyUseCase"] = None,
        seed_default_labor_roles: Optional["SeedDefaultLaborRolesUseCase"] = None,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker
        self._access_repo = access_repo
        self._seed_payment_methods = seed_payment_methods
        self._seed_default_labor_roles = seed_default_labor_roles

    def execute(
        self,
        inp: CreateCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> CompanyResponse:
        # 1. Validate inputs
        legal_name = _validate_legal_name(inp.legal_name)
        address = _validate_address(inp.address)
        _validate_prefix_override(inp.prefix_override)

        # 2. Build and persist entity
        now = datetime.now(timezone.utc)
        company = Company(
            id=uuid4(),
            legal_name=legal_name,
            address=address,
            siret=inp.siret,
            tva_number=inp.tva_number,
            iban=inp.iban,
            bic=inp.bic,
            logo_url=inp.logo_url,
            default_payment_terms=inp.default_payment_terms,
            prefix_override=inp.prefix_override,
            created_by=inp.caller_id,
            created_at=now,
            updated_at=now,
        )
        saved = self._company_repo.save(company)

        # 3. Attach the creator as company admin (D6) — is_primary when this
        # is their first company. Same transaction as the company insert so
        # a company is never created "orphaned" (no admin) if this fails.
        if self._access_repo is not None:
            is_first_company = len(self._access_repo.list_for_user(inp.caller_id)) == 0
            self._access_repo.save(
                UserCompanyAccess(
                    user_id=inp.caller_id,
                    company_id=saved.id,
                    is_primary=is_first_company,
                    attached_at=now,
                    role=CompanyRole.ADMIN.value,
                )
            )

        db_session.commit()

        # 4. Post-commit: seed builtin payment methods + default labor roles.
        # Must NOT raise — failure is non-fatal and must not roll back the company.
        if self._seed_payment_methods is not None:
            try:
                self._seed_payment_methods.execute(
                    company_id=saved.id,
                    legal_name=saved.legal_name,
                    created_by=inp.caller_id,
                    db_session=db_session,
                )
            except Exception:
                _log.exception(
                    "Payment method seed failed for company %s — company was created successfully",
                    saved.id,
                )

        if self._seed_default_labor_roles is not None:
            try:
                self._seed_default_labor_roles.execute(company_id=saved.id)
            except Exception:
                _log.exception(
                    "Default labor role seed failed for company %s — company was created successfully",
                    saved.id,
                )

        return CompanyResponse.from_entity(saved)
