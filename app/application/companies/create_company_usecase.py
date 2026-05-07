"""CreateCompanyUseCase — admin creates a new company entity."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.application.companies._helpers import (
    _assert_admin,
    _validate_address,
    _validate_legal_name,
    _validate_prefix_override,
)
from app.application.companies.dtos import CompanyResponse, CreateCompanyInput
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.companies.company import Company

_ADMIN_PERMISSION = "*:*"


class CreateCompanyUseCase:
    """Create a new company.

    Pre-conditions:
      - Caller must hold *:* permission (admin).
      - legal_name and address are required non-blank strings.
      - prefix_override, when supplied, must match ^[A-Z0-9]{1,8}$.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: CreateCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> CompanyResponse:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        # N1: UUID(int=0) as sentinel for "no company yet" — passing caller_id was misleading
        _assert_admin(inp.caller_id, UUID(int=0), is_admin)

        # 2. Validate inputs
        legal_name = _validate_legal_name(inp.legal_name)
        address = _validate_address(inp.address)
        _validate_prefix_override(inp.prefix_override)

        # 3. Build and persist entity
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
        db_session.commit()
        return CompanyResponse.from_entity(saved)
